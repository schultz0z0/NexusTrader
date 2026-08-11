import copy
import json
import urllib.request
import unittest
from unittest.mock import patch

from scripts import nexus_trade_smoke as smoke_module


try:
    from scripts.nexus_trade_smoke import (
        NexusTradeSmoke,
        SmokeSafetyError,
        TransportError,
        UrllibTransport,
    )
except ModuleNotFoundError:
    NexusTradeSmoke = None
    SmokeSafetyError = RuntimeError
    TransportError = RuntimeError
    UrllibTransport = None


API_KEY = "dashboard-secret-value"


def canonical_snapshot(**overrides):
    snapshot = {
        "schema_version": 1,
        "snapshot_version": 7,
        "bot_id": "nexus-trade",
        "runtime": {
            "champion_version_id": "champion-redacted",
            "trial_version_id": "trial-redacted",
            "champion_enabled": 0,
            "champion_account_id": "",
            "champion_account_type": "demo",
            "emergency_stop": 0,
        },
        "emergency_stop": False,
        "lanes": [
            {
                "lane": "champion_baseline",
                "version": {
                    "id": "champion-redacted",
                    "version_hash": "a" * 64,
                    "snapshot": {
                        "symbol": "R_100",
                        "timeframe_seconds": 60,
                        "duration_seconds": 58,
                        "bollinger": {"period": 20, "std_dev": 2, "ma": "SMA"},
                        "adx": {"period": 14, "max_entry": 22},
                    },
                },
            },
            {
                "lane": "challenger_trial",
                "version": {
                    "id": "trial-redacted",
                    "version_hash": "b" * 64,
                    "snapshot": {
                        "symbol": "R_100",
                        "timeframe_seconds": 60,
                        "duration_seconds": 58,
                        "bollinger": {"period": 20, "std_dev": 2, "ma": "SMA"},
                        "adx": {"period": 14, "max_entry": 22},
                    },
                },
            },
        ],
        "active_campaigns": [
            {
                "id": "campaign-redacted",
                "lane": "challenger_trial",
                "nexus_version_id": "trial-redacted",
                "status": "ACTIVE",
            }
        ],
        "decisions": [],
        "trades": [],
        "reports": [],
        "proposals": [],
    }
    snapshot.update(overrides)
    return snapshot


class FakeHttpTransport:
    def __init__(self, responses):
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self.calls = []

    def get_json(self, path, *, headers, timeout):
        self.calls.append((path, dict(headers), timeout))
        response = self.responses[path]
        if len(response) > 1:
            item = response.pop(0)
        else:
            item = response[0]
        if isinstance(item, Exception):
            raise item
        return item


def healthy_responses(snapshot=None):
    return {
        "/api/v1/health/live": {"status": "alive"},
        "/api/v1/health/ready": {"status": "ready"},
        "/api/v1/bots/nexus-trade": {
            "status": "success",
            "data": {
                "id": "nexus-trade",
                "strategy_id": "nexus_trade",
                "account_type": "demo",
                "symbol": "R_100",
                "timeframe_seconds": 60,
                "duration": 58,
                "duration_unit": "s",
                "initial_stake": 0.35,
            },
        },
        "/api/v1/nexus-trade": {"status": "success", "data": snapshot or canonical_snapshot()},
        "/api/v1/nexus-trade/versions": {"status": "success", "data": [{"id": "v"}]},
        "/api/v1/nexus-trade/campaigns": {"status": "success", "data": [{"id": "c"}]},
        "/api/v1/nexus-trade/reports": {"status": "success", "data": []},
        "/api/v1/nexus-trade/exports": {"status": "success", "data": []},
    }


class NexusTradeSmokeOfflineTests(unittest.TestCase):
    def require_subject(self):
        self.assertIsNotNone(
            NexusTradeSmoke,
            "scripts.nexus_trade_smoke must implement the offline smoke contract",
        )

    def test_redirect_handler_rejects_loopback_and_external_targets_without_forwarding_key(self):
        """Catches delegating any 3xx into a new request carrying dashboard authority."""
        handler_type = getattr(smoke_module, "FailClosedRedirectHandler", None)
        self.assertIsNotNone(handler_type)
        handler = handler_type()
        request = urllib.request.Request(
            "http://127.0.0.1:8990/api/v1/health/live",
            headers={"X-API-Key": API_KEY},
        )
        delegated = []

        def capture_delegate(*args, **kwargs):
            delegated.append((args, kwargs))

        with patch.object(
            urllib.request.HTTPRedirectHandler,
            "redirect_request",
            side_effect=capture_delegate,
        ):
            for code, target in (
                (300, "http://127.0.0.1:8990/redirected"),
                (399, "https://outside.invalid/collect"),
            ):
                with self.subTest(code=code), self.assertRaisesRegex(
                    TransportError,
                    "redirect_refused",
                ) as caught:
                    handler.redirect_request(request, None, code, "redirect", {}, target)
                self.assertNotIn(API_KEY, str(caught.exception))

        self.assertEqual(delegated, [])

    def test_transport_revalidates_effective_response_url_before_reading_payload(self):
        """Catches an injected/future opener returning data from a non-loopback URL."""

        class SyntheticResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://outside.invalid/collect"

            def read(self, _limit):
                return b'{"status":"alive"}'

        class SyntheticOpener:
            def open(self, *_args, **_kwargs):
                return SyntheticResponse()

        transport = UrllibTransport("http://127.0.0.1:8990")
        opener = SyntheticOpener()
        object.__setattr__(transport, "_opener", opener)

        with patch.object(urllib.request, "urlopen", side_effect=opener.open):
            with self.assertRaisesRegex(TransportError, "effective_url_not_loopback"):
                transport.get_json(
                    "/api/v1/health/live",
                    headers={"X-API-Key": API_KEY},
                    timeout=1.0,
                )

    def test_refuses_real_or_noncanonical_nexus_profile(self):
        """Catches removal of REAL/profile fail-closed checks."""
        self.require_subject()
        unsafe = canonical_snapshot()
        unsafe["runtime"]["champion_account_type"] = "real"
        unsafe["lanes"][0]["version"]["snapshot"]["duration_seconds"] = 60
        smoke = NexusTradeSmoke(FakeHttpTransport(healthy_responses(unsafe)), API_KEY)

        with self.assertRaises(SmokeSafetyError):
            smoke.run_read_only()

    def test_read_only_contract_uses_header_and_redacts_authorities(self):
        """Catches query-string auth or summaries that echo credentials/identities."""
        self.require_subject()
        transport = FakeHttpTransport(healthy_responses())
        smoke = NexusTradeSmoke(transport, API_KEY)

        summary = smoke.run_read_only()

        self.assertEqual(summary["outcome"], "PASS_READ_ONLY")
        self.assertEqual(summary["lanes"], 2)
        self.assertEqual(summary["campaigns"], 1)
        self.assertEqual(summary["reports"], 0)
        self.assertEqual(summary["exports"], 0)
        self.assertTrue(all(call[1] == {"X-API-Key": API_KEY} for call in transport.calls))
        serialized = json.dumps(summary).lower()
        self.assertNotIn(API_KEY.lower(), serialized)
        self.assertNotIn("account_id", serialized)
        self.assertNotIn("ticket", serialized)
        self.assertNotIn("token", serialized)

    def test_read_only_refuses_champion_on_even_without_demo_observation(self):
        """Catches a standard smoke proving DEMO profile while Champion remains armed."""
        champion_on = canonical_snapshot()
        champion_on["runtime"]["champion_enabled"] = 1
        smoke = NexusTradeSmoke(
            FakeHttpTransport(healthy_responses(champion_on)),
            API_KEY,
        )

        with self.assertRaisesRegex(SmokeSafetyError, "champion_must_be_off"):
            smoke.run_read_only()

    def test_transient_transport_failure_reconnects_with_a_bounded_retry(self):
        """Catches unbounded retry loops and failure to reconnect once."""
        self.require_subject()
        responses = healthy_responses()
        responses["/api/v1/health/live"] = [
            TransportError("temporary failure containing dashboard-secret-value"),
            {"status": "alive"},
        ]
        transport = FakeHttpTransport(responses)
        smoke = NexusTradeSmoke(transport, API_KEY, max_attempts=2)

        summary = smoke.run_read_only()

        live_calls = [call for call in transport.calls if call[0].endswith("/health/live")]
        self.assertEqual(len(live_calls), 2)
        self.assertEqual(summary["reconnects"], 1)
        self.assertNotIn(API_KEY, json.dumps(summary))

    def test_observation_returns_no_signal_without_weakening_rules(self):
        """Catches fabricated success when the bounded window contains no approved signal."""
        self.require_subject()
        transport = FakeHttpTransport({
            "/api/v1/nexus-trade": [
                {"status": "success", "data": canonical_snapshot()},
                {"status": "success", "data": canonical_snapshot()},
            ]
        })
        smoke = NexusTradeSmoke(transport, API_KEY)

        summary = smoke.observe_demo(polls=2)

        self.assertEqual(summary["outcome"], "NO_SIGNAL")
        self.assertEqual(summary["approved_signals"], 0)
        self.assertEqual(summary["new_contracts"], 0)

    def test_observation_reads_nested_runtime_decision_and_correlates_new_contract(self):
        """Catches treating the repository payload wrapper as an absent approval."""
        baseline = canonical_snapshot()
        observed = canonical_snapshot()
        observed["decisions"] = [{
            "id": "decision-1",
            "payload": {
                "decision": {
                    "decision_id": "decision-1",
                    "contract_type": "CALL",
                    "blocked_reason": None,
                },
            },
        }]
        observed["trades"] = [{
            "contract_id": 301,
            "decision_id": "decision-1",
            "lane": "challenger_trial",
            "stake": 0.35,
        }]
        smoke = NexusTradeSmoke(FakeHttpTransport({
            "/api/v1/nexus-trade": [
                {"status": "success", "data": baseline},
                {"status": "success", "data": observed},
            ],
        }), API_KEY)

        summary = smoke.observe_demo(polls=2)

        self.assertEqual(summary["outcome"], "PASS_DEMO_OBSERVATION")
        self.assertEqual(summary["approved_signals"], 1)
        self.assertEqual(summary["new_contracts"], 1)

    def test_observation_refuses_new_contract_without_correlated_approval(self):
        """Catches accepting a DEMO contract that has no observed approved decision."""
        baseline = canonical_snapshot()
        observed = canonical_snapshot(trades=[{
            "contract_id": 302,
            "decision_id": "missing-decision",
            "lane": "challenger_trial",
            "stake": 0.35,
        }])
        smoke = NexusTradeSmoke(FakeHttpTransport({
            "/api/v1/nexus-trade": [
                {"status": "success", "data": baseline},
                {"status": "success", "data": observed},
            ],
        }), API_KEY)

        with self.assertRaisesRegex(SmokeSafetyError, "approved_decision"):
            smoke.observe_demo(polls=2)

    def test_observation_requires_null_block_and_exact_demo_stake(self):
        """Catches empty blocked reasons or a non-canonical stake being treated as safe."""
        for blocked_reason, stake, expected in (
            ("", 0.35, "approved_decision"),
            (None, 0.36, "trade_stake_not_demo_minimum"),
        ):
            with self.subTest(blocked_reason=blocked_reason, stake=stake):
                baseline = canonical_snapshot()
                observed = canonical_snapshot()
                observed["decisions"] = [{
                    "id": "decision-2",
                    "payload": {"decision": {
                        "decision_id": "decision-2",
                        "contract_type": "PUT",
                        "blocked_reason": blocked_reason,
                    }},
                }]
                observed["trades"] = [{
                    "contract_id": 303,
                    "decision_id": "decision-2",
                    "lane": "challenger_trial",
                    "stake": stake,
                }]
                smoke = NexusTradeSmoke(FakeHttpTransport({
                    "/api/v1/nexus-trade": [
                        {"status": "success", "data": baseline},
                        {"status": "success", "data": observed},
                    ],
                }), API_KEY)

                with self.assertRaisesRegex(SmokeSafetyError, expected):
                    smoke.observe_demo(polls=2)

    def test_demo_observation_requires_champion_off_and_protected_demo_bot(self):
        """Catches observation armed against Champion ON or a non-0.35 DEMO bot."""
        self.require_subject()
        unsafe_bot = healthy_responses()
        unsafe_bot["/api/v1/bots/nexus-trade"]["data"]["initial_stake"] = 1.0
        smoke = NexusTradeSmoke(FakeHttpTransport(unsafe_bot), API_KEY)
        with self.assertRaises(SmokeSafetyError):
            smoke.run_read_only()

        champion_on = canonical_snapshot()
        champion_on["runtime"]["champion_enabled"] = 1
        smoke = NexusTradeSmoke(
            FakeHttpTransport({
                "/api/v1/nexus-trade": {"status": "success", "data": champion_on},
            }),
            API_KEY,
        )
        with self.assertRaises(SmokeSafetyError):
            smoke.observe_demo(polls=1)

    def test_observation_refuses_duplicate_or_parallel_lane_contracts(self):
        """Catches duplicate ownership or more than one active contract per lane."""
        self.require_subject()
        duplicate = canonical_snapshot()
        duplicate["trades"] = [
            {"contract_id": 101, "lane": "champion_baseline", "status": "open"},
            {"contract_id": 102, "lane": "champion_baseline", "status": "open"},
        ]
        smoke = NexusTradeSmoke(
            FakeHttpTransport({
                "/api/v1/nexus-trade": {"status": "success", "data": duplicate},
            }),
            API_KEY,
        )

        with self.assertRaises(SmokeSafetyError):
            smoke.observe_demo(polls=1)

    def test_restart_comparison_requires_same_versions_campaign_and_no_duplicate(self):
        """Catches loss of durable lane pointers/campaign or duplicate contracts on restart."""
        self.require_subject()
        before = canonical_snapshot()
        after = canonical_snapshot(snapshot_version=8)
        after["trades"] = [
            {"contract_id": 201, "lane": "challenger_trial", "status": "closed"}
        ]
        smoke = NexusTradeSmoke(FakeHttpTransport({}), API_KEY)

        result = smoke.verify_restart(before, after)

        self.assertEqual(result["outcome"], "PASS_RESTART")
        changed = canonical_snapshot(snapshot_version=8)
        changed["lanes"][1]["version"]["version_hash"] = "c" * 64
        with self.assertRaises(SmokeSafetyError):
            smoke.verify_restart(before, changed)

    def test_restart_refuses_snapshot_or_durable_counter_decrease(self):
        """Catches a restart accepting rollback or loss of durable journal rows."""
        before = canonical_snapshot(
            snapshot_version=8,
            decisions=[{"id": "decision-1"}, {"id": "decision-2"}],
            trades=[
                {"contract_id": 401, "lane": "champion_baseline", "status": "closed"},
                {"contract_id": 402, "lane": "challenger_trial", "status": "closed"},
            ],
            reports=[{"id": "report-1"}, {"id": "report-2"}],
            proposals=[{"id": "proposal-1"}, {"id": "proposal-2"}],
        )
        cases = {
            "snapshot_version": canonical_snapshot(**{
                **before,
                "snapshot_version": 7,
            }),
            "decisions": canonical_snapshot(**{
                **before,
                "decisions": before["decisions"][:1],
            }),
            "trades": canonical_snapshot(**{
                **before,
                "trades": before["trades"][:1],
            }),
            "reports": canonical_snapshot(**{
                **before,
                "reports": before["reports"][:1],
            }),
            "proposals": canonical_snapshot(**{
                **before,
                "proposals": before["proposals"][:1],
            }),
        }

        for field, after in cases.items():
            with self.subTest(field=field), self.assertRaisesRegex(
                SmokeSafetyError,
                "decreased",
            ):
                NexusTradeSmoke.verify_restart(before, after)

    def test_restart_refuses_missing_or_malformed_durable_counters(self):
        """Catches treating absent/non-list durable collections as a zero counter."""
        before = canonical_snapshot()
        for field, malformed in (
            ("reports", None),
            ("proposals", "not-a-list"),
        ):
            after = canonical_snapshot()
            after[field] = malformed
            with self.subTest(field=field), self.assertRaisesRegex(
                SmokeSafetyError,
                "durable_counter_invalid",
            ):
                NexusTradeSmoke.verify_restart(before, after)

    def test_restart_refuses_non_integer_snapshot_versions(self):
        """Catches coercing bool, string or float versions into durable revisions."""
        for invalid in (None, True, "8", 8.0):
            after = canonical_snapshot(snapshot_version=invalid)
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                SmokeSafetyError,
                "snapshot_version_invalid",
            ):
                NexusTradeSmoke.verify_restart(canonical_snapshot(), after)

    def test_restart_refuses_lane_identity_or_configuration_drift(self):
        """Catches pointer identity loss hidden by matching version hashes."""
        before = canonical_snapshot()
        cases = {}

        missing_lane = copy.deepcopy(before)
        missing_lane["lanes"] = missing_lane["lanes"][:1]
        cases["missing"] = missing_lane

        duplicate_lane = copy.deepcopy(before)
        duplicate_lane["lanes"][1]["lane"] = "champion_baseline"
        cases["duplicate"] = duplicate_lane

        changed_id = copy.deepcopy(before)
        changed_id["runtime"]["trial_version_id"] = "different-trial-version"
        changed_id["lanes"][1]["version"]["id"] = "different-trial-version"
        changed_id["active_campaigns"][0]["nexus_version_id"] = "different-trial-version"
        cases["version_id"] = changed_id

        changed_configuration = copy.deepcopy(before)
        changed_configuration["lanes"][1]["version"]["snapshot"]["adx"]["max_entry"] = 21
        cases["configuration"] = changed_configuration

        for mutation, after in cases.items():
            with self.subTest(mutation=mutation), self.assertRaises(SmokeSafetyError):
                NexusTradeSmoke.verify_restart(before, after)

    def test_restart_refuses_malformed_duplicate_or_changed_active_campaign(self):
        """Catches set-collapsed campaign rows and loss of trial campaign provenance."""
        before = canonical_snapshot()
        cases = {}

        duplicate = copy.deepcopy(before)
        duplicate["active_campaigns"].append(copy.deepcopy(duplicate["active_campaigns"][0]))
        cases["duplicate"] = duplicate

        missing_version = copy.deepcopy(before)
        del missing_version["active_campaigns"][0]["nexus_version_id"]
        cases["missing_version"] = missing_version

        wrong_lane = copy.deepcopy(before)
        wrong_lane["active_campaigns"][0]["lane"] = "champion_baseline"
        cases["wrong_lane"] = wrong_lane

        changed_id = copy.deepcopy(before)
        changed_id["active_campaigns"][0]["id"] = "different-campaign"
        cases["changed_id"] = changed_id

        changed_version = copy.deepcopy(before)
        changed_version["active_campaigns"][0]["nexus_version_id"] = "champion-redacted"
        cases["changed_version"] = changed_version

        for mutation, after in cases.items():
            with self.subTest(mutation=mutation), self.assertRaises(SmokeSafetyError):
                NexusTradeSmoke.verify_restart(before, after)

    def test_restart_refuses_runtime_pointer_mode_or_emergency_drift_without_account_leak(self):
        """Catches runtime identity drift while keeping account values out of diagnostics."""
        account_sentinel = "demo-account-private-sentinel"
        before = canonical_snapshot()
        before["runtime"]["champion_account_id"] = account_sentinel
        cases = {
            "champion_version_id": "changed-champion-version",
            "trial_version_id": "changed-trial-version",
            "champion_enabled": 1,
            "champion_account_id": "different-private-account",
            "champion_account_type": "real",
            "emergency_stop": 1,
        }

        for field, changed_value in cases.items():
            after = copy.deepcopy(before)
            after["runtime"][field] = changed_value
            if field == "emergency_stop":
                after["emergency_stop"] = True
            with self.subTest(field=field):
                with self.assertRaises(SmokeSafetyError) as caught:
                    NexusTradeSmoke.verify_restart(before, after)
                self.assertNotIn(account_sentinel, str(caught.exception))
                self.assertNotIn(str(changed_value), str(caught.exception))

        unchanged = copy.deepcopy(before)
        unchanged["snapshot_version"] += 1
        summary = NexusTradeSmoke.verify_restart(before, unchanged)
        self.assertNotIn(account_sentinel, json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
