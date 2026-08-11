"""Fail-closed, read-only NexusTrade Phase 1 smoke validation."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


NEXUS_BOT_ID = "nexus-trade"
NEXUS_SYMBOL = "R_100"
NEXUS_TIMEFRAME_SECONDS = 60
NEXUS_DURATION_SECONDS = 58
NEXUS_DEMO_STAKE = 0.35
NEXUS_LANES = {"champion_baseline", "challenger_trial"}


class SmokeSafetyError(RuntimeError):
    """A fail-closed safety or durability invariant was not proven."""


class TransportError(RuntimeError):
    """A sanitized transient local transport error."""


def _is_loopback_http_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


class FailClosedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect before urllib can construct a successor request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError("redirect_refused")


@dataclass(frozen=True)
class UrllibTransport:
    base_url: str
    _opener: Any = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if not _is_loopback_http_url(self.base_url):
            raise SmokeSafetyError("base_url_must_be_loopback_http")
        if parsed.query:
            raise SmokeSafetyError("base_url_must_not_contain_authority_or_query")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(
            self,
            "_opener",
            urllib.request.build_opener(FailClosedRedirectHandler()),
        )

    def get_json(self, path: str, *, headers: dict[str, str], timeout: float) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers=headers,
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                effective_url = response.geturl()
                if not isinstance(effective_url, str) or not _is_loopback_http_url(effective_url):
                    raise TransportError("effective_url_not_loopback")
                if response.status != 200:
                    raise TransportError(f"http_status_{response.status}")
                payload = response.read(4 * 1024 * 1024 + 1)
                if len(payload) > 4 * 1024 * 1024:
                    raise TransportError("response_too_large")
                decoded = json.loads(payload.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise TransportError("response_not_object")
                return decoded
        except urllib.error.HTTPError as exc:
            raise TransportError(f"http_status_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise TransportError("local_transport_unavailable") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TransportError("invalid_json_response") from None


class NexusTradeSmoke:
    READ_ONLY_ENDPOINTS = (
        ("versions", "/api/v1/nexus-trade/versions"),
        ("campaigns", "/api/v1/nexus-trade/campaigns"),
        ("reports", "/api/v1/nexus-trade/reports"),
        ("exports", "/api/v1/nexus-trade/exports"),
    )
    DURABLE_COLLECTIONS = ("decisions", "trades", "reports", "proposals")
    RUNTIME_IDENTITY_FIELDS = (
        "champion_version_id",
        "trial_version_id",
        "champion_enabled",
        "champion_account_id",
        "champion_account_type",
        "emergency_stop",
    )

    def __init__(
        self,
        transport,
        api_key: str,
        *,
        max_attempts: int = 2,
        request_timeout: float = 5.0,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise SmokeSafetyError("dashboard_key_missing")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 0 < request_timeout <= 10:
            raise ValueError("request_timeout must be between 0 and 10 seconds")
        self.transport = transport
        self._headers = {"X-API-Key": api_key}
        self.max_attempts = max_attempts
        self.request_timeout = request_timeout
        self.reconnects = 0

    def _fetch(self, path: str) -> dict:
        for attempt in range(self.max_attempts):
            try:
                return self.transport.get_json(
                    path,
                    headers=self._headers,
                    timeout=self.request_timeout,
                )
            except TransportError:
                if attempt + 1 == self.max_attempts:
                    raise SmokeSafetyError("local_transport_failed_closed") from None
                self.reconnects += 1
        raise SmokeSafetyError("local_transport_failed_closed")

    @staticmethod
    def _unwrap(response: dict, name: str) -> Any:
        if not isinstance(response, dict):
            raise SmokeSafetyError(f"{name}_response_invalid")
        if response.get("status") == "success":
            return response.get("data")
        if name.startswith("health_"):
            return response
        raise SmokeSafetyError(f"{name}_response_failed_closed")

    @classmethod
    def _validate_snapshot(cls, snapshot: dict) -> None:
        if not isinstance(snapshot, dict):
            raise SmokeSafetyError("snapshot_not_object")
        if snapshot.get("schema_version") != 1 or snapshot.get("bot_id") != NEXUS_BOT_ID:
            raise SmokeSafetyError("singleton_identity_invalid")
        if type(snapshot.get("snapshot_version")) is not int or snapshot["snapshot_version"] < 1:
            raise SmokeSafetyError("snapshot_version_invalid")
        runtime = snapshot.get("runtime")
        if not isinstance(runtime, dict) or runtime.get("champion_account_type") != "demo":
            raise SmokeSafetyError("runtime_not_provably_demo")
        if any(
            not isinstance(runtime.get(field), str) or not runtime[field]
            for field in ("champion_version_id", "trial_version_id")
        ):
            raise SmokeSafetyError("runtime_version_pointer_invalid")
        if not isinstance(runtime.get("champion_account_id"), str):
            raise SmokeSafetyError("runtime_account_identity_invalid")
        if type(runtime.get("champion_enabled")) is not int or runtime["champion_enabled"] != 0:
            raise SmokeSafetyError("champion_must_be_off")
        if type(runtime.get("emergency_stop")) is not int or runtime["emergency_stop"] not in {0, 1}:
            raise SmokeSafetyError("runtime_emergency_stop_invalid")
        if snapshot.get("emergency_stop") != bool(runtime.get("emergency_stop", 0)):
            raise SmokeSafetyError("emergency_stop_mismatch")

        lanes = snapshot.get("lanes")
        if not isinstance(lanes, list) or any(not isinstance(row, dict) for row in lanes):
            raise SmokeSafetyError("lane_singleton_contract_invalid")
        lane_names = [row.get("lane") for row in lanes]
        if set(lane_names) != NEXUS_LANES:
            raise SmokeSafetyError("lane_singleton_contract_invalid")
        if len(lane_names) != len(set(lane_names)):
            raise SmokeSafetyError("lane_duplicate_detected")
        lane_versions = {}
        for lane in lanes:
            version = lane.get("version")
            profile = version.get("snapshot") if isinstance(version, dict) else None
            if not isinstance(profile, dict):
                raise SmokeSafetyError("version_profile_missing")
            version_id = version.get("id")
            if not isinstance(version_id, str) or not version_id:
                raise SmokeSafetyError("version_id_invalid")
            expected = {
                "symbol": NEXUS_SYMBOL,
                "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
                "duration_seconds": NEXUS_DURATION_SECONDS,
            }
            if any(profile.get(field) != value for field, value in expected.items()):
                raise SmokeSafetyError("nexus_profile_not_canonical")
            version_hash = version.get("version_hash")
            if not isinstance(version_hash, str) or len(version_hash) != 64:
                raise SmokeSafetyError("version_hash_invalid")
            lane_versions[lane["lane"]] = version_id
        if (
            runtime["champion_version_id"] != lane_versions["champion_baseline"]
            or runtime["trial_version_id"] != lane_versions["challenger_trial"]
        ):
            raise SmokeSafetyError("runtime_lane_pointer_mismatch")

        campaigns = snapshot.get("active_campaigns")
        if not isinstance(campaigns, list) or not campaigns:
            raise SmokeSafetyError("active_campaign_missing")
        campaign_ids = []
        campaign_lanes = []
        for row in campaigns:
            if not isinstance(row, dict):
                raise SmokeSafetyError("active_campaign_schema_invalid")
            campaign_id = row.get("id")
            version_id = row.get("nexus_version_id")
            lane = row.get("lane")
            if not isinstance(campaign_id, str) or not campaign_id:
                raise SmokeSafetyError("active_campaign_schema_invalid")
            if not isinstance(version_id, str) or not version_id:
                raise SmokeSafetyError("active_campaign_schema_invalid")
            if lane not in NEXUS_LANES:
                raise SmokeSafetyError("active_campaign_lane_invalid")
            if row.get("status") != "ACTIVE":
                raise SmokeSafetyError("active_campaign_status_invalid")
            campaign_ids.append(campaign_id)
            campaign_lanes.append(lane)
        if len(campaign_ids) != len(set(campaign_ids)):
            raise SmokeSafetyError("duplicate_active_campaign_detected")
        if len(campaign_lanes) != len(set(campaign_lanes)):
            raise SmokeSafetyError("active_campaign_lane_duplicate")
        if campaign_lanes != ["challenger_trial"]:
            raise SmokeSafetyError("active_campaign_contract_invalid")
        if campaigns[0]["nexus_version_id"] != lane_versions["challenger_trial"]:
            raise SmokeSafetyError("active_campaign_version_mismatch")
        cls._validate_journals(snapshot)

    @staticmethod
    def _validate_journals(snapshot: dict) -> None:
        decisions = snapshot.get("decisions")
        trades = snapshot.get("trades")
        if not isinstance(decisions, list) or not isinstance(trades, list):
            raise SmokeSafetyError("journal_shape_invalid")
        decision_ids = [row.get("id") for row in decisions if row.get("id") is not None]
        if len(decision_ids) != len(set(decision_ids)):
            raise SmokeSafetyError("duplicate_decision_detected")
        contract_ids = [row.get("contract_id") for row in trades if row.get("contract_id") is not None]
        if len(contract_ids) != len(set(contract_ids)):
            raise SmokeSafetyError("duplicate_contract_detected")
        active_by_lane = Counter()
        for trade in trades:
            lane = trade.get("lane")
            if lane not in NEXUS_LANES:
                raise SmokeSafetyError("trade_lane_invalid")
            stake = trade.get("stake")
            if stake is not None and float(stake) != NEXUS_DEMO_STAKE:
                raise SmokeSafetyError("trade_stake_not_demo_minimum")
            if trade.get("symbol") not in {None, NEXUS_SYMBOL}:
                raise SmokeSafetyError("trade_symbol_invalid")
            if trade.get("duration") not in {None, NEXUS_DURATION_SECONDS}:
                raise SmokeSafetyError("trade_duration_invalid")
            if str(trade.get("status", "")).lower() not in {"closed", "won", "lost", "sold"}:
                active_by_lane[lane] += 1
        if any(count > 1 for count in active_by_lane.values()):
            raise SmokeSafetyError("parallel_lane_contracts_detected")

    @staticmethod
    def _validate_bot(bot: dict) -> None:
        expected = {
            "id": NEXUS_BOT_ID,
            "strategy_id": "nexus_trade",
            "account_type": "demo",
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "duration": NEXUS_DURATION_SECONDS,
            "duration_unit": "s",
            "initial_stake": NEXUS_DEMO_STAKE,
        }
        if not isinstance(bot, dict) or any(bot.get(key) != value for key, value in expected.items()):
            raise SmokeSafetyError("protected_demo_bot_profile_invalid")

    def run_read_only(self) -> dict:
        live = self._unwrap(self._fetch("/api/v1/health/live"), "health_live")
        ready = self._unwrap(self._fetch("/api/v1/health/ready"), "health_ready")
        if live.get("status") not in {"alive", "ok"}:
            raise SmokeSafetyError("liveness_failed")
        if ready.get("status") not in {"ready", "ok"}:
            raise SmokeSafetyError("readiness_failed")
        snapshot = self._unwrap(self._fetch("/api/v1/nexus-trade"), "snapshot")
        self._validate_snapshot(snapshot)
        bot = self._unwrap(self._fetch("/api/v1/bots/nexus-trade"), "bot")
        self._validate_bot(bot)

        counts = {}
        for name, endpoint in self.READ_ONLY_ENDPOINTS:
            rows = self._unwrap(self._fetch(endpoint), name)
            if not isinstance(rows, list):
                raise SmokeSafetyError(f"{name}_not_list")
            counts[name] = len(rows)
        if counts["versions"] < 1 or counts["campaigns"] < 1:
            raise SmokeSafetyError("version_or_campaign_missing")
        return {
            "outcome": "PASS_READ_ONLY",
            "snapshot_version": snapshot["snapshot_version"],
            "lanes": len(snapshot["lanes"]),
            "campaigns": len(snapshot["active_campaigns"]),
            "versions": counts["versions"],
            "reports": counts["reports"],
            "exports": counts["exports"],
            "reconnects": self.reconnects,
        }

    def observe_demo(self, *, polls: int, poll_interval: float = 0.0) -> dict:
        if not 1 <= polls <= 240:
            raise ValueError("polls must be between 1 and 240")
        if not 0 <= poll_interval <= 60:
            raise ValueError("poll_interval must be between 0 and 60 seconds")
        baseline_contracts: set[Any] | None = None
        new_contracts: dict[str, set[Any]] = {lane: set() for lane in NEXUS_LANES}
        approved_signals: set[Any] = set()
        for index in range(polls):
            snapshot = self._unwrap(self._fetch("/api/v1/nexus-trade"), "snapshot")
            self._validate_snapshot(snapshot)
            if snapshot["runtime"].get("champion_enabled") != 0:
                raise SmokeSafetyError("demo_observation_requires_champion_off")
            for stored in snapshot["decisions"]:
                payload = stored.get("payload")
                decision = (
                    payload.get("decision", {})
                    if isinstance(payload, dict)
                    else stored
                )
                decision_id = (
                    decision.get("decision_id")
                    or decision.get("id")
                    or stored.get("id")
                )
                if (
                    decision.get("contract_type") in {"CALL", "PUT"}
                    and decision.get("blocked_reason") is None
                    and decision_id is not None
                ):
                    approved_signals.add(decision_id)
            current_contracts = {
                row.get("contract_id")
                for row in snapshot["trades"]
                if row.get("contract_id") is not None
            }
            if baseline_contracts is None:
                baseline_contracts = current_contracts
            for trade in snapshot["trades"]:
                contract_id = trade.get("contract_id")
                if contract_id is not None and contract_id not in baseline_contracts:
                    if trade.get("stake") != 0.35:
                        raise SmokeSafetyError("new_contract_requires_exact_demo_stake")
                    metadata = trade.get("metadata") or {}
                    decision_id = trade.get("decision_id") or metadata.get("decision_id")
                    if decision_id not in approved_signals:
                        raise SmokeSafetyError("new_contract_requires_approved_decision")
                    new_contracts[trade["lane"]].add(contract_id)
            if any(len(contracts) > 1 for contracts in new_contracts.values()):
                raise SmokeSafetyError("more_than_one_smoke_contract_per_lane")
            if index + 1 < polls and poll_interval:
                time.sleep(poll_interval)
        total_contracts = sum(len(items) for items in new_contracts.values())
        if total_contracts:
            outcome = "PASS_DEMO_OBSERVATION"
        elif approved_signals:
            outcome = "SIGNAL_WITHOUT_CONTRACT"
        else:
            outcome = "NO_SIGNAL"
        return {
            "outcome": outcome,
            "approved_signals": len(approved_signals),
            "new_contracts": total_contracts,
            "reconnects": self.reconnects,
        }

    @classmethod
    def verify_restart(cls, before: dict, after: dict) -> dict:
        cls._validate_snapshot(before)
        cls._validate_snapshot(after)
        if after["snapshot_version"] != before["snapshot_version"]:
            raise SmokeSafetyError("snapshot_version_changed_on_restart")
        durable_counts = {}
        for field in cls.DURABLE_COLLECTIONS:
            before_rows = before.get(field)
            after_rows = after.get(field)
            if not isinstance(before_rows, list) or not isinstance(after_rows, list):
                raise SmokeSafetyError("durable_counter_invalid")
            durable_counts[field] = (len(before_rows), len(after_rows))
            if len(after_rows) < len(before_rows):
                raise SmokeSafetyError(f"{field}_decreased_on_restart")
        before_versions = {
            row["lane"]: {
                field: row["version"][field]
                for field in ("id", "version_hash", "snapshot")
            }
            for row in before["lanes"]
        }
        after_versions = {
            row["lane"]: {
                field: row["version"][field]
                for field in ("id", "version_hash", "snapshot")
            }
            for row in after["lanes"]
        }
        if before_versions != after_versions:
            raise SmokeSafetyError("lane_version_changed_on_restart")
        before_campaigns = [
            (row["id"], row["lane"], row["nexus_version_id"], row["status"])
            for row in before["active_campaigns"]
        ]
        after_campaigns = [
            (row["id"], row["lane"], row["nexus_version_id"], row["status"])
            for row in after["active_campaigns"]
        ]
        if before_campaigns != after_campaigns:
            raise SmokeSafetyError("campaign_changed_on_restart")
        for field in cls.RUNTIME_IDENTITY_FIELDS:
            if before["runtime"].get(field) != after["runtime"].get(field):
                raise SmokeSafetyError("runtime_identity_changed_on_restart")
        return {
            "outcome": "PASS_RESTART",
            "snapshot_version_before": before["snapshot_version"],
            "snapshot_version_after": after["snapshot_version"],
            "lanes": len(after["lanes"]),
            "campaigns": len(after["active_campaigns"]),
            **{
                f"{field}_{suffix}": counts[index]
                for field, counts in durable_counts.items()
                for index, suffix in enumerate(("before", "after"))
            },
        }


def _require_safe_process_environment(environment: dict[str, str]) -> None:
    if environment.get("ALLOW_REAL_TRADING", "").strip().lower() != "false":
        raise SmokeSafetyError("allow_real_trading_not_explicitly_false")
    if environment.get("DERIV_ACCOUNT_TYPE", "").strip().lower() != "demo":
        raise SmokeSafetyError("deriv_account_type_not_explicitly_demo")
    stake = environment.get("NEXUS_DEMO_STAKE", str(NEXUS_DEMO_STAKE)).strip()
    try:
        if float(stake) != NEXUS_DEMO_STAKE:
            raise SmokeSafetyError("nexus_demo_stake_not_exact")
    except ValueError:
        raise SmokeSafetyError("nexus_demo_stake_not_exact") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8990")
    parser.add_argument("--api-key-env", default="DASHBOARD_API_KEY")
    parser.add_argument("--demo-only", action="store_true")
    parser.add_argument("--observe-seconds", type=int, default=65)
    parser.add_argument("--poll-seconds", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _require_safe_process_environment(dict(os.environ))
        if args.demo_only and args.observe_seconds < 60:
            raise SmokeSafetyError("demo_observation_must_cover_one_m1")
        if args.poll_seconds < 1 or args.poll_seconds > 60:
            raise SmokeSafetyError("poll_seconds_out_of_bounds")
        api_key = os.environ.get(args.api_key_env, "")
        smoke = NexusTradeSmoke(UrllibTransport(args.base_url), api_key)
        summary = smoke.run_read_only()
        if args.demo_only:
            polls = max(2, args.observe_seconds // args.poll_seconds + 1)
            summary["demo"] = smoke.observe_demo(
                polls=polls,
                poll_interval=args.poll_seconds,
            )
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except (SmokeSafetyError, ValueError):
        print('{"outcome":"REFUSED_SAFE"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
