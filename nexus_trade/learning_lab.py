"""Durable deterministic NexusTrade learning laboratory."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from database.models import DatabaseModels
from database.nexus_models import NexusModels
from database.repository import DatabaseRepository
from core.events import runtime_event
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.constants import (
    NEXUS_PROVENANCE_HASH,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.dataset import DatasetBuilder, DatasetRejectedError
from nexus_trade.promotion import PromotionService
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
            "snapshot_version": transition.get("snapshot_version"),
            "events": transition.get("events", []),
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
        return {
            "job_type": "daily_learning",
            "campaign_id": campaign["id"],
            "trial_version_id": campaign["nexus_version_id"],
            "window_end_utc": window.end_utc.isoformat(),
            "outcome": "CANDIDATES_TRAINED" if candidates else "INSUFFICIENT_DATA",
            "candidates": candidates,
            "rejected_families": rejected,
        }

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
