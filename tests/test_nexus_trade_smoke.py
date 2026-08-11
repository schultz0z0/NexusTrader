import json
import unittest


try:
    from scripts.nexus_trade_smoke import (
        NexusTradeSmoke,
        SmokeSafetyError,
        TransportError,
    )
except ModuleNotFoundError:
    NexusTradeSmoke = None
    SmokeSafetyError = RuntimeError
    TransportError = RuntimeError


API_KEY = "dashboard-secret-value"


def canonical_snapshot(**overrides):
    snapshot = {
        "schema_version": 1,
        "snapshot_version": 7,
        "bot_id": "nexus-trade",
        "runtime": {
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


if __name__ == "__main__":
    unittest.main()
