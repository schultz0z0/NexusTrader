"""Durable deterministic NexusTrade learning laboratory."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import uuid
from itertools import combinations
from contextlib import contextmanager
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from statistics import NormalDist, mean, stdev

from database.models import DatabaseModels
from database.nexus_models import NexusModels
from database.repository import DatabaseRepository
from core.events import runtime_event
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.artifacts import CandidateArtifact, canonical_json
from nexus_trade.constants import (
    NEXUS_PROVENANCE_HASH,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.dataset import DatasetBuilder, DatasetRejectedError
from nexus_trade.promotion import PromotionService
from nexus_trade.reports import ReportService
from nexus_trade.scheduler import BrasiliaSchedule, ReportWindow
from nexus_trade.training import (
    SQLiteTrialLedger,
    Trainer,
    TrainingConfig,
    TrainingRejectedError,
)
from utils.logger import setup_logger


UTC = timezone.utc
logger = setup_logger("NexusLearningLab")

FEATURE_FAMILIES = (
    (
        "bollinger_adx_candle",
        (
            "adx", "bollinger_percent_b", "bollinger_z_score",
            "bollinger_width", "bollinger_slope", "adx_pdi", "adx_mdi",
            "body", "body_ratio", "upper_wick", "lower_wick",
            "upper_wick_ratio", "lower_wick_ratio",
        ),
        "indicator_reconfiguration",
    ),
    (
        "regime_volatility",
        (
            "adx", "bollinger_percent_b", "bollinger_z_score",
            "bollinger_width", "bollinger_slope", "chop", "atr", "atrp",
        ),
        "indicator_addition",
    ),
    (
        "momentum_reversion",
        (
            "adx", "bollinger_percent_b", "bollinger_z_score",
            "bollinger_width", "rsi", "stoch_k", "stoch_d", "cci", "roc",
        ),
        "indicator_addition",
    ),
    (
        "channels_trend",
        (
            "adx", "bollinger_percent_b", "bollinger_width",
            "keltner_upper", "keltner_center", "keltner_lower",
            "aroon_up", "aroon_down",
        ),
        "indicator_addition",
    ),
    (
        "moving_averages",
        (
            "adx", "bollinger_percent_b", "bollinger_width",
            "sma", "ema", "wma", "hma", "kama",
        ),
        "indicator_addition",
    ),
)


class LearningLabService:
    """Run persisted daily learning jobs outside the M1 dispatch path."""

    def __init__(self, db_path: str, *, minimum_rows: int = 30):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        if type(minimum_rows) is not int or minimum_rows < 30:
            raise ValueError("minimum_rows must be at least 30")
        self.db_path = db_path
        self.minimum_rows = minimum_rows
        self.owner_id = uuid.uuid4().hex
        self.schedule = BrasiliaSchedule()
        self.repository = DatabaseRepository(db_path)
        self.ledger = SQLiteTrialLedger(db_path)
        self.registry = CandidateRegistry(db_path)
        with self._connection() as db:
            db.executescript(DatabaseModels.create_tables_sql())
            db.executescript(NexusModels.create_tables_sql())

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    async def run_due(self, *, now: datetime | None = None) -> list[dict]:
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        campaign = self._active_trial_campaign()
        since = self._last_completed_boundary(campaign["id"])
        if since is None:
            since = self._parse_timestamp(campaign["started_at"])
        completed = []
        for window in self.schedule.due_windows(since, instant):
            job_type = {
                "daily": "daily_learning",
                "weekly": "weekly_rotation",
            }.get(window.kind)
            if job_type is None:
                continue
            if not self._claim(job_type, campaign["id"], window, instant):
                continue
            try:
                result = (
                    await self._process_daily(campaign, window)
                    if window.kind == "daily"
                    else await self._process_weekly(campaign, window)
                )
            except asyncio.CancelledError:
                self._finish_job(
                    job_type, campaign["id"], window,
                    status="FAILED", error_code="cancelled",
                    result={"job_type": job_type, "outcome": "FAILED"},
                )
                raise
            except Exception as exc:
                result = {
                    "job_type": job_type,
                    "campaign_id": campaign["id"],
                    "window_end_utc": window.end_utc.isoformat(),
                    "outcome": "FAILED",
                    "error_code": type(exc).__name__,
                }
                self._finish_job(
                    job_type, campaign["id"], window,
                    status="FAILED", error_code=type(exc).__name__, result=result,
                )
                completed.append(result)
                continue
            self._finish_job(
                job_type, campaign["id"], window,
                status="COMPLETED", error_code=None, result=result,
            )
            completed.append(result)
            if window.kind == "weekly" and result.get("changed") is True:
                # The next overdue boundary belongs to the new frozen Trial campaign.
                # Reload it on the next scheduler pass instead of attributing work to
                # the campaign that was just superseded.
                break
        return completed

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        poll_seconds: float = 30.0,
        publisher=None,
    ) -> None:
        if not isinstance(stop_event, asyncio.Event):
            raise TypeError("stop_event must be an asyncio.Event")
        if (
            isinstance(poll_seconds, bool)
            or not isinstance(poll_seconds, (int, float))
            or not math.isfinite(float(poll_seconds))
            or float(poll_seconds) <= 0
        ):
            raise ValueError("poll_seconds must be positive")
        while not stop_event.is_set():
            try:
                results = await self.run_due()
                for result in results:
                    logger.info(
                        "NexusTrade learning job completed: type=%s outcome=%s",
                        result.get("job_type"),
                        result.get("outcome"),
                    )
                if results and publisher is not None:
                    for result in results:
                        for event in result.get("events", []):
                            await publisher.publish(event)
                    snapshot = await self.repository.get_nexus_control_snapshot()
                    for result in results:
                        identity = ":".join((
                            str(result.get("job_type") or "job"),
                            str(result.get("campaign_id") or "campaign"),
                            str(result.get("window_end_utc") or "window"),
                        ))
                        await publisher.publish(runtime_event(
                            "nexus.learning",
                            NEXUS_TRADE_BOT_ID,
                            snapshot_version=int(snapshot["snapshot_version"]),
                            payload={
                                "id": identity,
                                "result": {
                                    key: value for key, value in result.items()
                                    if key != "events"
                                },
                                "learning": snapshot.get("learning") or {},
                            },
                        ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "NexusTrade learning scheduler failed closed: %s",
                    type(exc).__name__,
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(poll_seconds))
            except asyncio.TimeoutError:
                pass

    async def _process_weekly(self, campaign: sqlite3.Row, window: ReportWindow) -> dict:
        self._record_weekly_promotion_evidence(campaign, window)
        report = ReportService(self.db_path).close_weekly(window)
        proposal = self._ensure_proposal(report)
        report_event = await self._report_event(report)
        candidate_id = self._best_qualified_shadow()
        request_id = (
            f"learning-weekly:{campaign['id']}:{window.end_utc.isoformat()}"
        )
        transition = await PromotionService(self.db_path).replace_trial(
            window.end_utc,
            actor="system:learning-lab",
            request_id=request_id,
            reason="automatic governed weekly Trial selection",
            candidate_id=candidate_id,
        )
        return {
            "job_type": "weekly_rotation",
            "campaign_id": campaign["id"],
            "window_end_utc": window.end_utc.isoformat(),
            "outcome": "REPLACED" if transition.get("changed") else "RETAINED",
            "changed": bool(transition.get("changed")),
            "reason": transition.get("reason"),
            "candidate_id": candidate_id,
            "report_id": report.id,
            "report_hash": report.report_hash,
            "proposal_id": None if proposal is None else proposal["id"],
            "snapshot_version": transition.get("snapshot_version"),
            "events": [
                report_event,
                *([] if proposal is None or not proposal["created"] else [proposal["event"]]),
                *transition.get("events", []),
            ],
        }

    def _record_weekly_promotion_evidence(
        self, campaign: Mapping, window: ReportWindow,
    ) -> dict | None:
        campaign_id = campaign["id"]
        trial_version_id = campaign["nexus_version_id"]
        started = self._parse_timestamp(campaign["started_at"])
        complete_days = max(
            0,
            (
                window.end_utc.astimezone(BrasiliaSchedule.timezone).date()
                - started.astimezone(BrasiliaSchedule.timezone).date()
            ).days,
        )
        end_epoch = int(window.end_utc.timestamp())
        start_epoch = int(window.start_utc.timestamp())
        with self._connection() as db:
            accumulated = ReportService._settlement_rows(
                db, "challenger_trial", None, end_epoch, campaign_id,
            )
            if complete_days < 7 or len(accumulated) < 300:
                return None
            trial_rows = ReportService._settlement_rows(
                db, "challenger_trial", start_epoch, end_epoch, campaign_id,
            )
            champion_campaign = ReportService._campaign_for_window(
                db, "champion_baseline", start_epoch, end_epoch,
            )
            champion_rows = ReportService._settlement_rows(
                db, "champion_baseline", start_epoch, end_epoch,
                champion_campaign["id"],
            )
            provenance_hash = ReportService._comparable_provenance_hash(
                db, start_epoch, end_epoch, champion_rows, trial_rows,
            )
            existing = self._promotion_evidence_attempt(
                campaign_id=campaign_id,
                trial_version_id=trial_version_id,
                provenance_hash=provenance_hash,
                window_end_utc=window.end_utc.isoformat(),
            )
            if existing is not None:
                return existing
            all_rows = [*champion_rows, *trial_rows]
            contract_ids = [row["contract_id"] for row in all_rows]
            unresolved_intents = db.execute(
                "SELECT COUNT(*) FROM order_intents WHERE bot_id=? AND "
                "LOWER(state) IN ('prepared','submitting','reconcile_pending','ambiguous')",
                (NEXUS_TRADE_BOT_ID,),
            ).fetchone()[0]
            open_trades = db.execute(
                "SELECT COUNT(*) FROM trades WHERE bot_id=? AND status!='closed'",
                (NEXUS_TRADE_BOT_ID,),
            ).fetchone()[0]
            malformed_delays = db.execute(
                "SELECT COUNT(*) FROM trades t JOIN nexus_decisions d ON d.id=t.decision_id "
                "WHERE t.bot_id=? AND t.status='closed' AND t.campaign_id IN (?,?) "
                "AND (COALESCE(t.entry_delay_ms,d.entry_delay_ms) IS NULL OR "
                "COALESCE(t.entry_delay_ms,d.entry_delay_ms)>2000)",
                (NEXUS_TRADE_BOT_ID, campaign_id, champion_campaign["id"]),
            ).fetchone()[0]
            risk_violations = db.execute(
                "SELECT COUNT(*) FROM trades WHERE bot_id=? AND status='closed' "
                "AND campaign_id IN (?,?) AND (symbol!='R_100' OR stake<=0 OR "
                "(lane='challenger_trial' AND ABS(stake-0.35)>0.0000001))",
                (NEXUS_TRADE_BOT_ID, campaign_id, champion_campaign["id"]),
            ).fetchone()[0]
            covered = db.execute(
                "SELECT COUNT(*) FROM trades t JOIN nexus_decisions d ON d.id=t.decision_id "
                "JOIN nexus_features f ON f.symbol=d.symbol "
                "AND f.open_epoch=d.signal_epoch AND f.nexus_version_id=d.nexus_version_id "
                "WHERE t.bot_id=? AND t.status='closed' AND t.campaign_id IN (?,?) "
                "AND COALESCE(d.signal_epoch,t.purchase_time)>=? "
                "AND COALESCE(d.signal_epoch,t.purchase_time)<?",
                (
                    NEXUS_TRADE_BOT_ID, campaign_id, champion_campaign["id"],
                    start_epoch, end_epoch,
                ),
            ).fetchone()[0]
            coverage = covered / len(all_rows) if all_rows else 0.0
            trial_frozen = bool(trial_rows) and all(
                row["nexus_version_id"] == trial_version_id for row in trial_rows
            )
            reproducible = bool(all_rows) and all(
                row["provenance_hash"] == provenance_hash for row in all_rows
            )

        champion_returns = self._normalized_returns(champion_rows)
        trial_returns = self._normalized_returns(trial_rows)
        paired_daily = self._paired_daily_returns(champion_rows, trial_rows)
        dsr_probability = self._deflated_positive_probability(trial_returns)
        pbo = self._probability_backtest_overfit(paired_daily)
        sensitivity = self._leave_one_block_sensitivity(paired_daily)
        regimes = self._temporal_regimes(trial_returns)
        change_family, ablation_passed = self._active_trial_change_evidence()
        integrity = {
            "trial_frozen": trial_frozen,
            "all_reconciled": unresolved_intents == 0 and open_trades == 0,
            "no_duplicates": len(contract_ids) == len(set(contract_ids)),
            "no_future_leakage": coverage >= 0.995,
            "dispatch_within_limit": malformed_delays == 0,
            "candle_coverage": float(coverage),
            "reproducible": reproducible,
            "risk_limits_ok": risk_violations == 0,
        }
        payload = {
            "schema_version": 1,
            "attempt_type": "promotion_evidence",
            "status": "SUCCEEDED",
            "campaign_id": campaign_id,
            "trial_version_id": trial_version_id,
            "window_end_utc": window.end_utc.isoformat(),
            "provenance_hash": provenance_hash,
            "seed": 0,
            "metrics": {
                "champion_rows": len(champion_returns),
                "trial_rows": len(trial_returns),
                "paired_days": len(paired_daily),
            },
            "promotion_evidence": {
                "integrity": integrity,
                "regimes": regimes,
                "dsr_probability": dsr_probability,
                "pbo": pbo,
                "sensitivity_passed": sensitivity,
                "change_families": [change_family],
                "bollinger_present": True,
                "new_indicator_ablation_passed": ablation_passed,
            },
        }
        self.ledger.record(payload)
        persisted = self._promotion_evidence_attempt(
            campaign_id=campaign_id,
            trial_version_id=trial_version_id,
            provenance_hash=provenance_hash,
            window_end_utc=window.end_utc.isoformat(),
        )
        if persisted is None:
            raise RuntimeError("promotion evidence was not durably recorded")
        return persisted

    def _promotion_evidence_attempt(
        self, *, campaign_id: str, trial_version_id: str,
        provenance_hash: str, window_end_utc: str,
    ) -> dict | None:
        matches = [
            item for item in self.ledger.list_attempts()
            if item.get("attempt_type") == "promotion_evidence"
            and (
                item.get("campaign_id"), item.get("trial_version_id"),
                item.get("provenance_hash"), item.get("window_end_utc"),
            ) == (campaign_id, trial_version_id, provenance_hash, window_end_utc)
        ]
        if len(matches) > 1:
            raise ValueError("promotion evidence identity is ambiguous")
        return None if not matches else matches[0]

    @staticmethod
    def _normalized_returns(rows: list[dict]) -> list[float]:
        values = []
        for row in rows:
            stake = row.get("stake")
            profit = row.get("profit")
            if (
                isinstance(stake, bool) or not isinstance(stake, (int, float))
                or isinstance(profit, bool) or not isinstance(profit, (int, float))
                or not math.isfinite(float(stake)) or float(stake) <= 0
                or not math.isfinite(float(profit))
            ):
                raise ValueError("settlement return evidence is invalid")
            values.append(float(profit) / float(stake))
        return values

    @staticmethod
    def _paired_daily_returns(
        champion_rows: list[dict], trial_rows: list[dict],
    ) -> list[dict]:
        buckets = {}
        for lane, rows in (("champion", champion_rows), ("trial", trial_rows)):
            for row in rows:
                epoch = row["decision_epoch"]
                local = datetime.fromtimestamp(epoch, UTC).astimezone(
                    BrasiliaSchedule.timezone,
                )
                day = local.date() if local.hour >= 10 else local.date() - timedelta(days=1)
                buckets.setdefault(day.isoformat(), {"champion": [], "trial": []})[lane].append(
                    float(row["profit"]) / float(row["stake"])
                )
        paired = []
        for day in sorted(buckets):
            block = buckets[day]
            if block["champion"] and block["trial"]:
                paired.append({
                    "day": day,
                    "champion": mean(block["champion"]),
                    "trial": mean(block["trial"]),
                })
        return paired

    def _deflated_positive_probability(self, values: list[float]) -> float:
        if len(values) < 30:
            return 0.0
        deviation = stdev(values)
        if deviation == 0:
            return 1.0 if mean(values) > 0 else 0.0
        z_score = mean(values) / (deviation / math.sqrt(len(values)))
        raw_probability = NormalDist().cdf(z_score)
        trial_count = max(1, len([
            item for item in self.ledger.list_attempts()
            if item.get("attempt_type") != "promotion_evidence"
        ]))
        return max(0.0, min(1.0, 1.0 - (1.0 - raw_probability) * trial_count))

    @staticmethod
    def _probability_backtest_overfit(blocks: list[dict]) -> float:
        count = len(blocks)
        if count < 7:
            return 1.0
        train_size = count // 2
        positive_train = 0
        failed_forward = 0
        indices = tuple(range(count))
        for selected in combinations(indices, train_size):
            selected_set = set(selected)
            train = [blocks[index]["trial"] - blocks[index]["champion"] for index in selected]
            test = [
                blocks[index]["trial"] - blocks[index]["champion"]
                for index in indices if index not in selected_set
            ]
            if mean(train) > 0:
                positive_train += 1
                if mean(test) <= 0:
                    failed_forward += 1
        return 1.0 if positive_train == 0 else failed_forward / positive_train

    @staticmethod
    def _leave_one_block_sensitivity(blocks: list[dict]) -> bool:
        if len(blocks) < 7:
            return False
        differences = [block["trial"] - block["champion"] for block in blocks]
        return all(mean(differences[:index] + differences[index + 1:]) > 0 for index in range(len(differences)))

    @staticmethod
    def _temporal_regimes(values: list[float]) -> list[dict]:
        if len(values) < 90:
            return []
        boundaries = (0, len(values) // 3, 2 * len(values) // 3, len(values))
        regimes = []
        for index in range(3):
            sample = values[boundaries[index]:boundaries[index + 1]]
            average = mean(sample)
            deviation = stdev(sample) if len(sample) > 1 else 0.0
            significant_loss = (
                average < 0
                and deviation > 0
                and average / (deviation / math.sqrt(len(sample))) <= -1.6448536269514722
            )
            regimes.append({
                "name": ("early", "middle", "recent")[index],
                "n": len(sample),
                "trial_loss_significant": significant_loss,
            })
        return regimes

    def _active_trial_change_evidence(self) -> tuple[str, bool]:
        trial = next(
            (item for item in self.registry.list_candidates() if item["status"] == "TRIAL"),
            None,
        )
        if trial is None or trial["id"] == "candidate-nexus-trial-v1":
            return "indicator_reconfiguration", True
        attempts = [
            item for item in self.ledger.list_attempts()
            if item.get("candidate_id") == trial["id"]
            and isinstance(item.get("qualification"), Mapping)
            and item["qualification"].get("status") == "PASS"
        ]
        if not attempts:
            return "indicator_reconfiguration", False
        latest = attempts[-1]
        change_family = latest.get("change_family", "indicator_reconfiguration")
        gates = latest["qualification"].get("gates", [])
        ablation = change_family != "indicator_addition" or any(
            isinstance(gate, Mapping)
            and gate.get("name") == "INDICATOR_ABLATION"
            and gate.get("status") == "PASS"
            for gate in gates
        )
        return change_family, ablation

    def _ensure_proposal(self, report) -> dict | None:
        snapshot = report.snapshot
        recommendation = snapshot.get("recommendation")
        if recommendation not in {"EVOLVE", "REANALYZE"}:
            return None
        progress = snapshot.get("accumulated_progress")
        if (
            not isinstance(progress, Mapping)
            or type(progress.get("operations")) is not int
            or progress["operations"] < 300
            or type(progress.get("complete_days")) is not int
            or progress["complete_days"] < 7
        ):
            return None
        trial = snapshot.get("trial")
        if isinstance(trial, Mapping):
            trial = dict(trial)
        required = {
            "candidate_id", "artifact_hash", "metadata_hash", "configuration_hash",
            "dataset_hash", "provenance_hash", "version_id", "version_hash",
        }
        if isinstance(trial, dict) and not trial.get("candidate_id") and not trial.get("artifact_hash"):
            return None
        if not isinstance(trial, dict) or any(not trial.get(name) for name in required):
            raise ValueError("eligible weekly report lacks executable Trial bindings")
        candidate_id = trial["candidate_id"]
        proposal_payload = {
            "report_id": report.id,
            "report_hash": report.report_hash,
            "candidate_id": candidate_id,
            "candidate_hash": trial["artifact_hash"],
            "artifact_hash": trial["artifact_hash"],
            "metadata_hash": trial["metadata_hash"],
            "configuration_hash": trial["configuration_hash"],
            "dataset_hash": trial["dataset_hash"],
            "provenance_hash": trial["provenance_hash"],
            "trial_version_id": trial["version_id"],
            "trial_version_hash": trial["version_hash"],
            "campaign_id": snapshot["campaign_id"],
        }
        proposal_id = f"proposal-{report.report_hash[:24]}"
        encoded = canonical_json(proposal_payload)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM nexus_proposals WHERE id=?", (proposal_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["campaign_id"] != snapshot["campaign_id"]
                    or existing["nexus_version_id"] != trial["version_id"]
                    or existing["payload"] != encoded
                ):
                    db.rollback()
                    raise ValueError("weekly proposal identity conflicts with persisted data")
                db.commit()
                return {
                    "id": proposal_id,
                    "status": existing["status"],
                    "created": False,
                    "event": None,
                }
            runtime = db.execute(
                "SELECT champion_version_id,trial_version_id FROM nexus_runtime "
                "WHERE bot_id=?",
                (NEXUS_TRADE_BOT_ID,),
            ).fetchone()
            candidate = db.execute(
                "SELECT * FROM nexus_candidates WHERE id=? AND status='TRIAL'",
                (candidate_id,),
            ).fetchone()
            version = db.execute(
                "SELECT version_hash FROM nexus_versions WHERE id=? AND status='TRIAL'",
                (trial["version_id"],),
            ).fetchone()
            if (
                runtime is None
                or runtime["trial_version_id"] != trial["version_id"]
                or candidate is None
                or candidate["artifact_hash"] != trial["artifact_hash"]
                or version is None
                or version["version_hash"] != trial["version_hash"]
            ):
                db.rollback()
                raise ValueError("weekly proposal Trial identity is no longer active")
            artifact = CandidateArtifact.from_json(candidate["metadata"])
            artifact.executable_gate()
            exact = {
                "artifact_hash": artifact.artifact_hash,
                "metadata_hash": artifact.metadata_hash,
                "configuration_hash": artifact.metadata["configuration_hash"],
                "dataset_hash": artifact.metadata["dataset_hash"],
                "provenance_hash": artifact.metadata["provenance_hash"],
            }
            if any(trial[name] != value for name, value in exact.items()):
                db.rollback()
                raise ValueError("weekly report and Trial artifact hashes diverge")
            revision_row = db.execute(
                "SELECT config_revision FROM bot_instances WHERE id=?",
                (NEXUS_TRADE_BOT_ID,),
            ).fetchone()
            if revision_row is None:
                db.rollback()
                raise ValueError("NexusTrade config revision is unavailable")
            revision = int(revision_row["config_revision"])
            db.execute(
                "INSERT INTO nexus_proposals "
                "(id,campaign_id,nexus_version_id,revision,status,payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    proposal_id, snapshot["campaign_id"], trial["version_id"], 1,
                    "PENDING_USER_REVIEW", encoded,
                ),
            )
            cas = db.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1,"
                " updated_at=CURRENT_TIMESTAMP WHERE id=? AND config_revision=?",
                (NEXUS_TRADE_BOT_ID, revision),
            )
            if cas.rowcount != 1:
                db.rollback()
                raise RuntimeError("NexusTrade revision changed during proposal creation")
            new_revision = revision + 1
            audit_id = "audit-proposal-" + hashlib.sha256(
                encoded.encode("utf-8")
            ).hexdigest()[:24]
            pointers = {
                "champion_version_id": runtime["champion_version_id"],
                "trial_version_id": runtime["trial_version_id"],
            }
            db.execute(
                "INSERT INTO nexus_audit_events "
                "(id,actor,action,reason,request_id,expected_revision,actual_revision,"
                "outcome,before_json,after_json,hashes_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    audit_id, "system:learning-lab", "CREATE_PROPOSAL",
                    "governed weekly recommendation requires human decision",
                    proposal_id, revision, new_revision, "COMMITTED",
                    canonical_json(pointers), canonical_json(pointers),
                    canonical_json({
                        "report_hash": report.report_hash,
                        "candidate_hash": trial["artifact_hash"],
                        **exact,
                    }),
                ),
            )
            event_payload = {
                "id": proposal_id,
                "campaign_id": snapshot["campaign_id"],
                "nexus_version_id": trial["version_id"],
                "revision": 1,
                "status": "PENDING_USER_REVIEW",
                "report_id": report.id,
                "recommendation": recommendation,
            }
            event_id = "nexus-proposal-" + hashlib.sha256(
                (proposal_id + "\0" + str(new_revision)).encode("utf-8")
            ).hexdigest()[:32]
            db.execute(
                "INSERT INTO nexus_event_outbox "
                "(event_id,action,request_id,event_type,snapshot_version,payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    event_id, "CREATE_PROPOSAL", proposal_id, "nexus.proposal",
                    new_revision, canonical_json(event_payload),
                ),
            )
            db.commit()
        event = runtime_event(
            "nexus.proposal",
            NEXUS_TRADE_BOT_ID,
            snapshot_version=new_revision,
            payload=event_payload,
        )
        event["event_id"] = event_id
        return {
            "id": proposal_id,
            "status": "PENDING_USER_REVIEW",
            "created": True,
            "event": event,
        }

    def _best_qualified_shadow(self) -> str | None:
        candidates = {
            item["id"]: item
            for item in self.registry.list_candidates()
            if item["status"] == "SHADOW"
        }
        qualified = {}
        for attempt in self.ledger.list_attempts():
            candidate_id = attempt.get("candidate_id")
            qualification = attempt.get("qualification")
            if (
                candidate_id not in candidates
                or attempt.get("status") != "SUCCEEDED"
                or not isinstance(qualification, dict)
                or qualification.get("status") != "PASS"
            ):
                continue
            gates = qualification.get("gates")
            if not isinstance(gates, list) or not gates or not all(
                isinstance(gate, dict) and gate.get("status") == "PASS"
                for gate in gates
            ):
                continue
            if attempt.get("artifact_hash") != candidates[candidate_id]["artifact_hash"]:
                continue
            test_metrics = attempt.get("metrics", {}).get("test", {})
            score = test_metrics.get("brier_score", 1.0)
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                score = 1.0
            qualified[candidate_id] = (float(score), int(attempt["id"]), candidate_id)
        if not qualified:
            return None
        return min(qualified.values())[2]

    async def _process_daily(self, campaign: sqlite3.Row, window: ReportWindow) -> dict:
        cutoff_epoch = int(window.end_utc.timestamp())
        candidates = []
        rejected = []
        for index, (family, feature_names, change_family) in enumerate(FEATURE_FAMILIES):
            rows = await self.repository.list_nexus_learning_rows(
                campaign_id=campaign["id"],
                cutoff_epoch=cutoff_epoch,
                feature_names=feature_names,
            )
            base = {
                "schema_version": 1,
                "status": "REJECTED",
                "campaign_id": campaign["id"],
                "trial_version_id": campaign["nexus_version_id"],
                "window_end_utc": window.end_utc.isoformat(),
                "provenance_hash": NEXUS_PROVENANCE_HASH,
                "seed": 73 + index,
                "feature_family": family,
                "feature_schema": list(feature_names),
                "change_family": change_family,
                "trial_count": 0,
                "metrics": {},
                "ablations": [],
            }
            if len(rows) < self.minimum_rows:
                self.ledger.record({
                    **base,
                    "error_code": "insufficient_complete_rows",
                    "complete_rows": len(rows),
                    "minimum_rows": self.minimum_rows,
                })
                rejected.append(family)
                continue
            try:
                dataset, artifact, candidate = await asyncio.to_thread(
                    self._train_candidate,
                    rows,
                    cutoff_epoch,
                    73 + index,
                )
            except (DatasetRejectedError, TrainingRejectedError) as exc:
                rejected.append(family)
                if isinstance(exc, DatasetRejectedError):
                    self.ledger.record({
                        **base,
                        "error_code": "dataset_rejected",
                        "complete_rows": len(rows),
                    })
                continue
            qualification = self._qualification(
                artifact=artifact,
                candidate=candidate,
                family=family,
                change_family=change_family,
            )
            self.ledger.record({
                **base,
                "status": (
                    "SUCCEEDED" if qualification["status"] == "PASS" else "REJECTED"
                ),
                "candidate_id": candidate["id"],
                "artifact_hash": candidate["artifact_hash"],
                "dataset_hash": artifact.metadata["dataset_hash"],
                "trial_count": artifact.metadata["trial_count"],
                "metrics": dict(artifact.metadata["metrics"]),
                "ablations": [dict(item) for item in artifact.metadata["ablations"]],
                "qualification": qualification,
            })
            candidates.append({
                "family": family,
                "change_family": change_family,
                "candidate_id": candidate["id"],
                "artifact_hash": candidate["artifact_hash"],
                "dataset_hash": artifact.metadata["dataset_hash"],
                "validation_brier_score": artifact.metadata["metrics"]["validation"]["brier_score"],
                "test_brier_score": artifact.metadata["metrics"]["test"]["brier_score"],
                "operate_count": artifact.metadata["metrics"]["test"]["operate_count"],
            })
        report = ReportService(self.db_path).close_daily(window)
        report_event = await self._report_event(report)
        return {
            "job_type": "daily_learning",
            "campaign_id": campaign["id"],
            "trial_version_id": campaign["nexus_version_id"],
            "window_end_utc": window.end_utc.isoformat(),
            "outcome": "CANDIDATES_TRAINED" if candidates else "INSUFFICIENT_DATA",
            "candidates": candidates,
            "rejected_families": rejected,
            "report_id": report.id,
            "report_hash": report.report_hash,
            "events": [report_event],
        }

    async def _report_event(self, report) -> dict:
        snapshot = await self.repository.get_nexus_control_snapshot()
        return runtime_event(
            "nexus.report",
            NEXUS_TRADE_BOT_ID,
            snapshot_version=int(snapshot["snapshot_version"]),
            payload={
                "id": report.id,
                "report_hash": report.report_hash,
                "report_type": report.snapshot["report_type"],
                "campaign_id": report.snapshot["campaign_id"],
                "window": dict(report.snapshot["window"]),
                "recommendation": report.snapshot["recommendation"],
            },
        )

    def _train_candidate(
        self,
        rows: list[dict],
        cutoff_epoch: int,
        seed: int,
    ):
        dataset = DatasetBuilder(
            rows,
            expected_provenance_hash=NEXUS_PROVENANCE_HASH,
            minimum_rows=self.minimum_rows,
        ).build(cutoff_epoch)
        artifact = Trainer(TrainingConfig(
            seed=seed,
            minimum_train_rows=max(20, int(self.minimum_rows * 0.6) - 2),
        )).fit(dataset, self.ledger)
        return dataset, artifact, self.registry.register(artifact)

    @staticmethod
    def _qualification(*, artifact, candidate: dict, family: str, change_family: str) -> dict:
        metadata = artifact.metadata
        metrics = metadata["metrics"]
        validation = metrics["validation"]
        test = metrics["test"]
        gates = []

        def gate(name: str, passed: bool, value, threshold: str) -> None:
            gates.append({
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "value": value,
                "threshold": threshold,
            })

        executable = True
        try:
            artifact.executable_gate()
        except (TypeError, ValueError):
            executable = False
        gate("ARTIFACT_EXECUTABLE", executable, executable, "true")
        gate("FORWARD_ROWS", test["rows"] >= 10, test["rows"], ">=10")
        for split_name, split in (("VALIDATION", validation), ("TEST", test)):
            score = split["brier_score"]
            gate(
                f"{split_name}_BRIER",
                isinstance(score, (int, float))
                and not isinstance(score, bool)
                and math.isfinite(float(score))
                and float(score) <= 0.25,
                float(score),
                "<=0.25",
            )
        gate(
            "OPERATE_SAMPLE",
            test["operate_count"] >= max(1, math.ceil(test["rows"] * 0.10)),
            test["operate_count"],
            ">=10% forward rows",
        )
        if change_family == "indicator_addition":
            useful = [
                item for item in metadata["ablations"]
                if isinstance(item.get("delta"), (int, float))
                and not isinstance(item.get("delta"), bool)
                and math.isfinite(float(item["delta"]))
                and float(item["delta"]) > 0.0
            ]
            gate("INDICATOR_ABLATION", bool(useful), len(useful), ">=1 useful feature")
        status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"
        return {
            "schema_version": 1,
            "status": status,
            "family": family,
            "change_family": change_family,
            "candidate_id": candidate["id"],
            "artifact_hash": candidate["artifact_hash"],
            "gates": gates,
        }

    def _active_trial_campaign(self) -> sqlite3.Row:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM nexus_campaigns WHERE lane='challenger_trial' "
                "AND status='ACTIVE' ORDER BY started_at,id"
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("learning lab requires exactly one active Trial campaign")
        return rows[0]

    def _last_completed_boundary(self, campaign_id: str) -> datetime | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT window_end_utc FROM nexus_learning_jobs "
                "WHERE campaign_id=? AND job_type='daily_learning' AND status='COMPLETED' "
                "ORDER BY window_end_utc DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
        return None if row is None else datetime.fromisoformat(row[0]).astimezone(UTC)

    def _claim(
        self, job_type: str, campaign_id: str, window: ReportWindow, now: datetime,
    ) -> bool:
        job_id = f"{job_type}:{campaign_id}:{window.end_utc.isoformat()}"
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO nexus_learning_jobs "
                "(id,job_type,campaign_id,window_start_utc,window_end_utc,status,owner_id,claimed_at_utc) "
                "VALUES (?,?,?,?,?,'RUNNING',?,?) ON CONFLICT DO NOTHING",
                (
                    job_id, job_type, campaign_id, window.start_utc.isoformat(),
                    window.end_utc.isoformat(), self.owner_id, now.isoformat(),
                ),
            )
            claimed = cursor.rowcount == 1
            if not claimed:
                lease_cutoff = (now - timedelta(minutes=5)).isoformat()
                cursor = db.execute(
                    "UPDATE nexus_learning_jobs SET status='RUNNING',owner_id=?,"
                    "claimed_at_utc=?,result_json=NULL,error_code=NULL,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND (status='FAILED' "
                    "OR (status='RUNNING' AND claimed_at_utc<=?))",
                    (self.owner_id, now.isoformat(), job_id, lease_cutoff),
                )
                claimed = cursor.rowcount == 1
            db.commit()
            return claimed

    def _finish_job(
        self,
        job_type: str,
        campaign_id: str,
        window: ReportWindow,
        *,
        status: str,
        error_code: str | None,
        result: dict,
    ) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE nexus_learning_jobs SET status=?,result_json=?,error_code=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE job_type=? AND campaign_id=? "
                "AND window_end_utc=? AND owner_id=? AND status='RUNNING'",
                (
                    status,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    error_code, job_type, campaign_id,
                    window.end_utc.isoformat(), self.owner_id,
                ),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise RuntimeError("learning job ownership changed before completion")
            db.commit()

    def list_attempts(self) -> list[dict]:
        return self.ledger.list_attempts()

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = ["FEATURE_FAMILIES", "LearningLabService"]
