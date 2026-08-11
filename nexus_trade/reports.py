"""Immutable, content-addressed NexusTrade daily and weekly snapshots."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from types import MappingProxyType
from typing import Any, Mapping

from nexus_trade.artifacts import canonical_json, validate_safe_json
from nexus_trade.gates import GateResult, PromotionGateEvaluator
from nexus_trade.metrics import calculate_lane_metrics
from nexus_trade.scheduler import BRASILIA, BrasiliaSchedule, ReportWindow


class ImmutableReportError(RuntimeError):
    """An aligned immutable report slot already contains different content."""


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ReportSnapshot:
    id: str
    report_hash: str
    snapshot: Mapping[str, Any]

    def as_dict(self) -> dict:
        return {"id": self.id, "report_hash": self.report_hash, "snapshot": _plain(self.snapshot)}


class ReportService:
    def __init__(self, db_path: str, *, gate_evaluator: PromotionGateEvaluator | None = None):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        self.gate_evaluator = gate_evaluator or PromotionGateEvaluator()
        with self._connection() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_reports (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT,
                    report_hash TEXT NOT NULL UNIQUE,
                    snapshot TEXT NOT NULL DEFAULT '{}',
                    report_type TEXT,
                    window_start_utc TEXT,
                    window_end_utc TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing = {row[1] for row in db.execute("PRAGMA table_info(nexus_reports)")}
            for name in ("report_type", "window_start_utc", "window_end_utc"):
                if name not in existing:
                    db.execute(f"ALTER TABLE nexus_reports ADD COLUMN {name} TEXT")
            db.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_reports_aligned_slot
                ON nexus_reports(report_type, window_end_utc, campaign_id)
                WHERE report_type IS NOT NULL AND window_end_utc IS NOT NULL;
                CREATE TRIGGER IF NOT EXISTS trg_nexus_reports_no_update
                BEFORE UPDATE ON nexus_reports
                BEGIN SELECT RAISE(ABORT, 'Nexus reports are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_nexus_reports_no_delete
                BEFORE DELETE ON nexus_reports
                BEGIN SELECT RAISE(ABORT, 'Nexus reports are immutable'); END;
                """
            )
            db.commit()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    def close_daily(self, window: ReportWindow, evidence: Mapping[str, Any] | None = None) -> ReportSnapshot:
        return self._close("daily", window, evidence)

    def close_weekly(self, window: ReportWindow, evidence: Mapping[str, Any] | None = None) -> ReportSnapshot:
        return self._close("weekly", window, evidence)

    def _close(self, kind: str, window: ReportWindow, evidence: Mapping[str, Any] | None) -> ReportSnapshot:
        if type(window) is not ReportWindow or window.kind != kind:
            raise ValueError(f"{kind} close requires an aligned {kind} ReportWindow")
        material = dict(evidence) if isinstance(evidence, Mapping) else self._gather_evidence(window)
        snapshot = self._build_snapshot(kind, window, material)
        validate_safe_json(snapshot)
        encoded = canonical_json(snapshot)
        import hashlib
        report_hash = hashlib.sha256(b"nexus-report-json-v1\0" + encoded.encode("utf-8")).hexdigest()
        report_id = f"report-{report_hash[:24]}"
        campaign_id = snapshot["campaign_id"]
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT * FROM nexus_reports
                   WHERE report_type=? AND window_end_utc=? AND campaign_id=?""",
                (kind, window.end_utc.isoformat(), campaign_id),
            ).fetchone()
            if existing is not None:
                db.commit()
                if existing["report_hash"] != report_hash or existing["snapshot"] != encoded:
                    raise ImmutableReportError("aligned report snapshot already exists with different content")
                return self._decode(existing)
            try:
                db.execute(
                    """
                    INSERT INTO nexus_reports (
                        id, campaign_id, report_hash, snapshot, report_type,
                        window_start_utc, window_end_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (report_id, campaign_id, report_hash, encoded, kind, window.start_utc.isoformat(), window.end_utc.isoformat()),
                )
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                row = db.execute("SELECT * FROM nexus_reports WHERE report_hash=?", (report_hash,)).fetchone()
                if row is None:
                    raise
                return self._decode(row)
        return ReportSnapshot(report_id, report_hash, _freeze(snapshot))

    def _build_snapshot(self, kind: str, window: ReportWindow, evidence: dict) -> dict:
        campaign_id = evidence.get("campaign_id")
        if type(campaign_id) is not str or not campaign_id:
            raise ValueError("report campaign_id is required")
        champion = self._lane(evidence.get("champion"), "champion_baseline", campaign_id)
        trial = self._lane(evidence.get("trial"), "challenger_trial", campaign_id)
        if champion["provenance_hash"] != trial["provenance_hash"]:
            raise ValueError("Champion and Trial report provenance must match")
        duplicate_contracts = set(champion.pop("contract_ids")) & set(trial.pop("contract_ids"))
        if duplicate_contracts:
            raise ValueError("duplicate contracts across Champion and Trial are forbidden")
        accumulated = evidence.get("trial_accumulated_operations", trial["metrics"]["n_total"])
        complete_days = evidence.get("complete_days", 0)
        if type(accumulated) is not int or accumulated < 0 or accumulated < trial["metrics"]["n_total"]:
            raise ValueError("Trial accumulated operation counter is invalid")
        if type(complete_days) is not int or complete_days < 0:
            raise ValueError("complete_days is invalid")

        supplied_gates = evidence.get("gates")
        recommendation = evidence.get("recommendation")
        if kind == "weekly" and isinstance(evidence.get("evaluation_context"), Mapping):
            evaluation = self.gate_evaluator.evaluate(
                calculate_lane_metrics(evidence["champion"]["settlements"]),
                calculate_lane_metrics(evidence["trial"]["settlements"]),
                evidence["evaluation_context"],
            )
            gates = [self._gate_dict(gate) for gate in evaluation.gates]
            recommendation = evaluation.recommendation
        elif isinstance(supplied_gates, (list, tuple)) and supplied_gates:
            gates = [self._gate_dict(gate) for gate in supplied_gates]
        else:
            code = "DAILY_NO_PROMOTION" if kind == "daily" else "EVIDENCE_INCOMPLETE"
            gates = [{
                "code": code, "status": "INCONCLUSIVE", "observed": None,
                "threshold": "governed weekly evidence", "reason": "daily reports are descriptive only" if kind == "daily" else "conservative gate evidence was not supplied",
            }]
            recommendation = "INCONCLUSIVE"
        if recommendation not in {"EVOLVE", "REANALYZE", "INCONCLUSIVE"}:
            raise ValueError("invalid machine recommendation")

        days = evidence.get("daily", [])
        if not isinstance(days, (list, tuple)):
            raise ValueError("daily rows must be a sequence")
        diffs = self._diffs(champion, trial)
        return {
            "schema_version": 1,
            "report_type": kind,
            "campaign_id": campaign_id,
            "window": window.as_dict(),
            "champion": champion,
            "trial": trial,
            "days": _plain(days),
            "full_totals": {"champion": champion["metrics"], "trial": trial["metrics"]},
            "accumulated_progress": {
                "operations": accumulated,
                "target": 300,
                "complete_days": complete_days,
                "required_days": 7,
                "eligible_count": accumulated >= 300,
                "eligible_days": complete_days >= 7,
            },
            "diffs": diffs,
            "gates": gates,
            "recommendation": recommendation,
            "recommendation_reasons": [gate["reason"] for gate in gates if gate["status"] != "PASS"],
            "audit": _plain(evidence.get("audit", [])),
            "disclosure": "Historical campaign performance is not a guarantee of future results.",
        }

    @staticmethod
    def _lane(value: Any, lane: str, campaign_id: str) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError(f"{lane} evidence is required")
        required = (
            "version_id", "version_hash", "configuration", "feature_schema",
            "entry_rules", "model", "settlements", "symbol", "timeframe_seconds",
            "duration_seconds", "provenance_hash",
        )
        if any(name not in value for name in required):
            raise ValueError(f"{lane} evidence is incomplete")
        if (value["symbol"], value["timeframe_seconds"], value["duration_seconds"]) != ("R_100", 60, 58):
            raise ValueError("reports require the R_100/M1/58s contract")
        settlements = list(value["settlements"])
        ids = []
        for row in settlements:
            contract_id = row.get("contract_id") if isinstance(row, Mapping) else getattr(row, "contract_id", None)
            if isinstance(contract_id, bool) or type(contract_id) is not int or contract_id <= 0:
                raise ValueError("settlement contract_id must be a positive integer")
            ids.append(contract_id)
            row_lane = row.get("lane") if isinstance(row, Mapping) else getattr(row, "lane", None)
            row_campaign = row.get("campaign_id") if isinstance(row, Mapping) else getattr(row, "campaign_id", None)
            row_version = row.get("nexus_version_id") if isinstance(row, Mapping) else getattr(row, "nexus_version_id", None)
            if row_lane is not None and row_lane != lane:
                raise ValueError("cross-lane settlement provenance")
            if row_campaign is not None and row_campaign != campaign_id:
                raise ValueError("cross-campaign settlement provenance")
            if row_version is not None and row_version != value["version_id"]:
                raise ValueError("cross-version settlement provenance")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate settlement contract")
        metrics = calculate_lane_metrics(settlements)
        return {
            "lane": lane,
            "version_id": value["version_id"],
            "version_hash": value["version_hash"],
            "contract": {"symbol": "R_100", "timeframe_seconds": 60, "duration_seconds": 58},
            "provenance_hash": value["provenance_hash"],
            "configuration": _plain(value["configuration"]),
            "feature_schema": _plain(value["feature_schema"]),
            "entry_rules": _plain(value["entry_rules"]),
            "model": _plain(value["model"]),
            "metrics": metrics.as_dict(),
            "contract_ids": ids,
        }

    @staticmethod
    def _diffs(champion: dict, trial: dict) -> dict:
        champion_config = champion["configuration"]
        trial_config = trial["configuration"]
        c_keys, t_keys = set(champion_config), set(trial_config)
        indicators = {
            "added": sorted(t_keys - c_keys),
            "removed": sorted(c_keys - t_keys),
            "reconfigured": sorted(key for key in c_keys & t_keys if champion_config[key] != trial_config[key]),
        }
        return {
            "configuration": {"champion": champion_config, "trial": trial_config},
            "indicators": indicators,
            "features": {
                "added": sorted(set(trial["feature_schema"]) - set(champion["feature_schema"])),
                "removed": sorted(set(champion["feature_schema"]) - set(trial["feature_schema"])),
            },
            "entry_rules": {"champion": champion["entry_rules"], "trial": trial["entry_rules"]},
            "model": {"champion": champion["model"], "trial": trial["model"]},
        }

    @staticmethod
    def _gate_dict(gate: Any) -> dict:
        if type(gate) is GateResult:
            return {"code": gate.code, "status": gate.status, "observed": _plain(gate.observed), "threshold": _plain(gate.threshold), "reason": gate.reason}
        if isinstance(gate, Mapping):
            fields = {name: _plain(gate.get(name)) for name in ("code", "status", "observed", "threshold", "reason")}
            if fields["status"] not in {"PASS", "FAIL", "INCONCLUSIVE"} or not fields["code"] or not fields["reason"]:
                raise ValueError("gate result is incomplete")
            return fields
        raise ValueError("gate result is invalid")

    def get_weekly(self, aligned_week: str | date | datetime) -> ReportSnapshot | None:
        if isinstance(aligned_week, str):
            try:
                aligned_week = date.fromisoformat(aligned_week)
            except ValueError as exc:
                raise ValueError("week must be an ISO date") from exc
        if isinstance(aligned_week, date) and not isinstance(aligned_week, datetime):
            if aligned_week.weekday() != 0:
                raise ValueError("historical week lookup must align to Monday")
            end = datetime.combine(aligned_week, time(10), BRASILIA).astimezone(timezone.utc)
        elif isinstance(aligned_week, datetime):
            end = aligned_week.astimezone(timezone.utc)
            BrasiliaSchedule().weekly_window(end)
        else:
            raise TypeError("aligned_week must be an ISO Monday, date or aware datetime")
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM nexus_reports WHERE report_type='weekly' AND window_end_utc=? ORDER BY created_at, id LIMIT 1",
                (end.isoformat(),),
            ).fetchone()
        return None if row is None else self._decode(row)

    def get_report(self, report_id: str) -> ReportSnapshot | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM nexus_reports WHERE id=?", (report_id,)).fetchone()
        return None if row is None else self._decode(row)

    def list_reports(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT * FROM nexus_reports ORDER BY window_end_utc DESC, id DESC").fetchall()
        return [self._decode(row).as_dict() for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> ReportSnapshot:
        return ReportSnapshot(row["id"], row["report_hash"], _freeze(json.loads(row["snapshot"])))

    def _gather_evidence(self, window: ReportWindow) -> dict:
        start_epoch = int(window.start_utc.timestamp())
        end_epoch = int(window.end_utc.timestamp())
        with self._connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"nexus_runtime", "nexus_versions", "nexus_campaigns", "trades"}
            if not required.issubset(tables):
                raise ValueError("persisted NexusTrade reporting evidence is unavailable")
            runtime = db.execute("SELECT * FROM nexus_runtime WHERE bot_id='nexus-trade'").fetchone()
            if runtime is None:
                raise ValueError("NexusTrade runtime provenance is unavailable")
            campaign_rows = db.execute(
                """
                SELECT * FROM nexus_campaigns
                WHERE lane='challenger_trial'
                  AND CAST(strftime('%s', started_at) AS INTEGER) < ?
                  AND (ended_at IS NULL OR CAST(strftime('%s', ended_at) AS INTEGER) >= ?)
                ORDER BY started_at DESC, id DESC
                """,
                (end_epoch, start_epoch),
            ).fetchall()
            if len(campaign_rows) != 1:
                raise ValueError("report window must resolve exactly one Trial campaign")
            campaign = campaign_rows[0]
            campaign_id = campaign["id"]
            champion_rows = self._settlement_rows(db, "champion_baseline", start_epoch, end_epoch)
            trial_rows = self._settlement_rows(db, "challenger_trial", start_epoch, end_epoch, campaign_id)
            accumulated_trial = self._settlement_rows(db, "challenger_trial", None, end_epoch, campaign_id)
            champion_version = self._version_evidence(db, runtime["champion_version_id"])
            trial_version_id = runtime["trial_version_id"] or campaign["nexus_version_id"]
            if trial_rows:
                versions = {row.get("nexus_version_id") for row in trial_rows}
                if len(versions) != 1 or None in versions:
                    raise ValueError("Trial report rows cross or omit version provenance")
                trial_version_id = versions.pop()
            trial_version = self._version_evidence(db, trial_version_id)
            provenance_hash = self._window_provenance_hash(db, start_epoch, end_epoch)
            audit = []
            if "nexus_audit_events" in tables:
                audit = [
                    {"id": row["id"], "actor": row["actor"], "action": row["action"], "created_at": row["created_at"]}
                    for row in db.execute(
                        "SELECT id, actor, action, created_at FROM nexus_audit_events ORDER BY created_at, id"
                    ).fetchall()
                ]

        champion = {**champion_version, "settlements": champion_rows, "provenance_hash": provenance_hash}
        trial = {**trial_version, "settlements": trial_rows, "provenance_hash": provenance_hash}
        started = self._parse_db_datetime(campaign["started_at"])
        complete_days = max(0, (window.end_utc.astimezone(BRASILIA).date() - started.astimezone(BRASILIA).date()).days)
        return {
            "campaign_id": campaign_id,
            "champion": champion,
            "trial": trial,
            "complete_days": complete_days,
            "trial_accumulated_operations": len(accumulated_trial),
            "daily": self._daily_rows(champion_rows, trial_rows),
            "gates": [],
            "recommendation": "INCONCLUSIVE",
            "audit": audit + [{"actor": "system", "action": f"{window.kind.upper()}_REPORT_CLOSE"}],
        }

    @staticmethod
    def _parse_db_datetime(value: Any) -> datetime:
        if type(value) is not str:
            raise ValueError("campaign timestamp is invalid")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    @staticmethod
    def _settlement_rows(
        db: sqlite3.Connection,
        lane: str,
        start_epoch: int | None,
        end_epoch: int,
        campaign_id: str | None = None,
    ) -> list[dict]:
        decision_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nexus_decisions'"
        ).fetchone() is not None
        epoch_sql = "COALESCE(d.signal_epoch, t.purchase_time)" if decision_table else "t.purchase_time"
        join_sql = "LEFT JOIN nexus_decisions d ON d.id=t.decision_id" if decision_table else ""
        query = f"""
            SELECT t.contract_id, t.stake, t.payout, t.profit, t.result,
                   t.lane, t.nexus_version_id, t.campaign_id,
                   {epoch_sql} AS decision_epoch
            FROM trades t {join_sql}
            WHERE t.bot_id='nexus-trade' AND t.status='closed' AND t.lane=?
              AND {epoch_sql} < ?
        """
        params: list[Any] = [lane, end_epoch]
        if start_epoch is not None:
            query += f" AND {epoch_sql} >= ?"
            params.append(start_epoch)
        if campaign_id is not None:
            query += " AND t.campaign_id=?"
            params.append(campaign_id)
        query += f" ORDER BY {epoch_sql}, t.contract_id"
        rows = []
        for row in db.execute(query, params).fetchall():
            rows.append({
                "contract_id": row["contract_id"],
                "stake": row["stake"],
                "payout": row["payout"],
                "profit": row["profit"],
                "result": row["result"],
                "settled": True,
                "lane": row["lane"],
                "nexus_version_id": row["nexus_version_id"],
                "campaign_id": row["campaign_id"],
                "decision_epoch": row["decision_epoch"],
            })
        return rows

    @staticmethod
    def _version_evidence(db: sqlite3.Connection, version_id: str) -> dict:
        row = db.execute("SELECT * FROM nexus_versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise ValueError("report version provenance is unavailable")
        snapshot = json.loads(row["snapshot"])
        configuration = snapshot.get("indicator_configuration", snapshot)
        features = snapshot.get("feature_schema", sorted(
            name for name, value in configuration.items() if isinstance(value, Mapping)
        ))
        entry_rules = snapshot.get("entry_rules", [snapshot.get("direction_source", "bollinger_v1_deterministic")])
        model = snapshot.get("model", "deterministic")
        return {
            "version_id": row["id"],
            "version_hash": row["version_hash"],
            "configuration": configuration,
            "feature_schema": features,
            "entry_rules": entry_rules,
            "model": model,
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "duration_seconds": 58,
        }

    @staticmethod
    def _window_provenance_hash(db: sqlite3.Connection, start_epoch: int, end_epoch: int) -> str:
        import hashlib

        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nexus_candles'"
        ).fetchone() is not None
        candles = []
        if exists:
            candles = [
                list(row) for row in db.execute(
                    """SELECT symbol, open_epoch, close_epoch, open, high, low, close
                       FROM nexus_candles WHERE symbol='R_100' AND open_epoch>=? AND open_epoch<?
                       ORDER BY open_epoch""",
                    (start_epoch, end_epoch),
                ).fetchall()
            ]
        payload = {"symbol": "R_100", "timeframe_seconds": 60, "start": start_epoch, "end": end_epoch, "candles": candles}
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _daily_rows(champion_rows: list[dict], trial_rows: list[dict]) -> list[dict]:
        by_day: dict[str, dict[str, list[dict]]] = {}
        for lane, rows in (("champion", champion_rows), ("trial", trial_rows)):
            for row in rows:
                epoch = row.get("decision_epoch")
                if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
                    raise ValueError("decision time provenance is required")
                day = datetime.fromtimestamp(epoch, timezone.utc).astimezone(BRASILIA).date().isoformat()
                by_day.setdefault(day, {"champion": [], "trial": []})[lane].append(row)
        return [
            {
                "date": day,
                "champion": calculate_lane_metrics(rows["champion"]).as_dict(),
                "trial": calculate_lane_metrics(rows["trial"]).as_dict(),
            }
            for day, rows in sorted(by_day.items())
        ]


__all__ = ["ImmutableReportError", "ReportService", "ReportSnapshot"]
