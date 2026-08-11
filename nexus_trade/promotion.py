"""Transactional, human-only governance for NexusTrade version transitions."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from nexus_trade.artifacts import (
    ArtifactIntegrityError,
    CandidateArtifact,
    canonical_json,
    validate_safe_json,
)
from nexus_trade.constants import NEXUS_TRADE_BOT_ID
from nexus_trade.scheduler import BRASILIA, BrasiliaSchedule


class PromotionError(RuntimeError):
    """Base class for governed transition failures."""


class PromotionRejected(PromotionError):
    """The requested transition does not satisfy governance."""


class PromotionConflict(PromotionError):
    """The caller's compare-and-swap revision is stale."""


_PROMOTION_GATE_CODES = {
    "MINIMUM_SAMPLE", "DATA_INTEGRITY", "COMPARABLE_PROVENANCE",
    "TRIAL_EXPECTANCY_POSITIVE", "PROFIT_FACTOR", "EXPECTANCY_IMPROVEMENT",
    "BLOCK_BOOTSTRAP_95", "DRAWDOWN", "RECOVERY", "WORST_ROLLING_50",
    "LOSS_STREAK", "RISK_LIMITS", "DAILY_STABILITY", "RECENT_STABILITY",
    "REGIME_STABILITY", "DSR", "PBO", "SENSITIVITY", "CHANGE_BUDGET",
}


class PromotionService:
    def __init__(self, db_path: str, *, failure_injector=None):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        self.failure_injector = failure_injector

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=30000")
            yield db

    async def approve(
        self,
        proposal_id: str,
        expected_revision: int,
        actor: str,
        *,
        request_id: str,
        reason: str,
        reinforced_confirmation: bool = False,
    ) -> dict:
        actor, request_id, reason = self._validate_identity(actor, request_id, reason)
        if type(proposal_id) is not str or not proposal_id.strip():
            raise ValueError("proposal_id is required")
        if isinstance(expected_revision, bool) or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")

        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            actual = await self._actual_revision(db)
            before = await self._pointers(db)
            input_hash = self._request_input_hash(
                "APPROVE",
                {
                    "proposal_id": proposal_id.strip(),
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                    "reinforced_confirmation": reinforced_confirmation,
                },
            )
            replay = await self._load_request(db, "APPROVE", request_id)
            if replay is not None:
                if replay["input_hash"] != input_hash:
                    await self._audit(
                        db, "APPROVE", actor, reason, request_id,
                        expected_revision, actual, "CONFLICTED", before, before,
                        error_code="REQUEST_ID_REUSED",
                    )
                    await db.commit()
                    raise PromotionConflict("request_id is already bound to different input")
                result = json.loads(replay["result_json"])
                await self._audit(
                    db, "APPROVE", actor, reason, request_id,
                    expected_revision, actual, "IDEMPOTENT", before, before,
                    error_code="REQUEST_REPLAY",
                )
                await db.commit()
                result["idempotent"] = True
                result["events"] = await self._events_for_request("APPROVE", request_id)
                return result
            if actual != expected_revision:
                await self._audit(
                    db, "APPROVE", actor, reason, request_id,
                    expected_revision, actual, "CONFLICTED", before, before,
                    error_code="STALE_REVISION",
                )
                await db.commit()
                raise PromotionConflict("stale NexusTrade revision")

            async with db.execute(
                "SELECT * FROM nexus_proposals WHERE id = ?", (proposal_id.strip(),)
            ) as cursor:
                proposal = await cursor.fetchone()
            if proposal is None:
                await self._audit(
                    db, "APPROVE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    error_code="PROPOSAL_NOT_FOUND",
                )
                await db.commit()
                raise PromotionRejected("promotion proposal does not exist")
            try:
                material = await self._validate_approval(
                    db, proposal, reinforced_confirmation=reinforced_confirmation,
                )
            except PromotionRejected as exc:
                await self._audit(
                    db, "APPROVE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    error_code=str(exc),
                )
                await db.commit()
                raise

            artifact = material["artifact"]
            candidate = material["candidate"]
            report = material["report"]
            promoted_snapshot = {
                "schema_version": 1,
                "candidate_id": candidate["id"],
                "artifact": json.loads(artifact.to_json()),
                "approval": {
                    "proposal_id": proposal["id"],
                    "report_id": report["id"],
                    "report_hash": report["report_hash"],
                },
            }
            encoded_snapshot = canonical_json(promoted_snapshot)
            version_hash = hashlib.sha256(
                b"nexus-champion-version-v1\0" + encoded_snapshot.encode("utf-8")
            ).hexdigest()
            version_id = f"nexus-v{actual + 1}-{version_hash[:12]}"
            await db.execute(
                "INSERT INTO nexus_versions (id,name,status,version_hash,snapshot) "
                "VALUES (?,?,?,?,?)",
                (version_id, f"Champion V{actual + 1}", "CHAMPION", version_hash, encoded_snapshot),
            )
            await self._fault_point(
                db, "after_version", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            await db.execute(
                "UPDATE nexus_campaigns SET status='CLOSED',ended_at=CURRENT_TIMESTAMP "
                "WHERE lane='champion_baseline' AND status='ACTIVE'"
            )
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,'ACTIVE')",
                (f"champion-{version_id}", "champion_baseline", version_id),
            )
            await db.execute(
                "UPDATE nexus_campaigns SET status='CLOSED',ended_at=CURRENT_TIMESTAMP "
                "WHERE lane='challenger_trial' AND status='ACTIVE'"
            )
            new_trial_campaign = f"trial-after-{request_id}-{artifact.artifact_hash[:12]}"
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,'ACTIVE')",
                (new_trial_campaign, "challenger_trial", material["runtime"]["trial_version_id"]),
            )
            await self._fault_point(
                db, "after_campaigns", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            await db.execute(
                "UPDATE nexus_runtime SET champion_version_id=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE bot_id=? AND champion_enabled=0",
                (version_id, NEXUS_TRADE_BOT_ID),
            )
            await self._fault_point(
                db, "after_pointer", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            proposal_update = await db.execute(
                "UPDATE nexus_proposals SET status='APPROVED',revision=revision+1 "
                "WHERE id=? AND revision=? AND status='PENDING_USER_REVIEW'",
                (proposal["id"], proposal["revision"]),
            )
            if proposal_update.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("proposal changed during approval")
            await self._fault_point(
                db, "after_proposal", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            cas = await db.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND strategy_id='nexus_trade' AND config_revision=?",
                (NEXUS_TRADE_BOT_ID, expected_revision),
            )
            if cas.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("stale NexusTrade revision")
            await self._fault_point(
                db, "after_cas", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            new_revision = actual + 1
            after = await self._pointers(db)
            hashes = {
                "report_hash": report["report_hash"],
                "candidate_hash": candidate["artifact_hash"],
                "artifact_hash": artifact.artifact_hash,
                "metadata_hash": artifact.metadata_hash,
                "configuration_hash": artifact.metadata["configuration_hash"],
                "version_hash": version_hash,
            }
            await self._audit(
                db, "APPROVE", actor, reason, request_id,
                expected_revision, new_revision, "COMMITTED", before, after,
                hashes=hashes,
            )
            await self._fault_point(
                db, "after_audit", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            events = [
                ("nexus.proposal", {"id": proposal["id"], "status": "APPROVED", "revision": proposal["revision"] + 1}),
                ("nexus.version_changed", {"lane": "champion_baseline", "version": {"id": version_id, "version_hash": version_hash}}),
                ("nexus.campaign", {"id": f"champion-{version_id}", "lane": "champion_baseline", "status": "ACTIVE", "nexus_version_id": version_id}),
            ]
            await self._write_events(db, "APPROVE", request_id, new_revision, events)
            await self._fault_point(
                db, "after_outbox", "APPROVE", actor, reason, request_id,
                expected_revision, actual, before,
            )
            result = {
                "action": "APPROVE",
                "outcome": "COMMITTED",
                "snapshot_version": new_revision,
                "before": before,
                "after": after,
            }
            await self._store_request(db, "APPROVE", request_id, input_hash, result)
            await db.commit()
            result["events"] = await self._events_for_request("APPROVE", request_id)
            return result

    async def reanalyze(
        self,
        proposal_id: str,
        expected_revision: int,
        actor: str,
        *,
        request_id: str,
        reason: str,
    ) -> dict:
        actor, request_id, reason = self._validate_identity(actor, request_id, reason)
        if type(proposal_id) is not str or not proposal_id.strip():
            raise ValueError("proposal_id is required")
        if isinstance(expected_revision, bool) or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            actual = await self._actual_revision(db)
            before = await self._pointers(db)
            input_hash = self._request_input_hash(
                "REANALYZE",
                {
                    "proposal_id": proposal_id.strip(),
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                },
            )
            replay = await self._load_request(db, "REANALYZE", request_id)
            if replay is not None:
                if replay["input_hash"] != input_hash:
                    await self._audit(
                        db, "REANALYZE", actor, reason, request_id,
                        expected_revision, actual, "CONFLICTED", before, before,
                        error_code="REQUEST_ID_REUSED",
                    )
                    await db.commit()
                    raise PromotionConflict("request_id is already bound to different input")
                result = json.loads(replay["result_json"])
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "IDEMPOTENT", before, before,
                    error_code="REQUEST_REPLAY",
                )
                await db.commit()
                result["idempotent"] = True
                result["events"] = await self._events_for_request("REANALYZE", request_id)
                return result
            if actual != expected_revision:
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "CONFLICTED", before, before,
                    error_code="STALE_REVISION",
                )
                await db.commit()
                raise PromotionConflict("stale NexusTrade revision")
            async with db.execute("SELECT * FROM nexus_proposals WHERE id=?", (proposal_id.strip(),)) as cursor:
                proposal = await cursor.fetchone()
            if proposal is None or proposal["status"] != "PENDING_USER_REVIEW":
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    error_code="PROPOSAL_NOT_PENDING",
                )
                await db.commit()
                raise PromotionRejected("proposal is not pending human review")
            try:
                payload = json.loads(proposal["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    error_code="PROPOSAL_CORRUPT",
                )
                await db.commit()
                raise PromotionRejected("proposal payload is corrupt") from exc
            if not isinstance(payload, dict):
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    error_code="PROPOSAL_CORRUPT",
                )
                await db.commit()
                raise PromotionRejected("proposal payload is corrupt")
            async with db.execute(
                "SELECT * FROM nexus_campaigns WHERE id=? AND lane='challenger_trial' AND status='ACTIVE'",
                (proposal["campaign_id"],),
            ) as cursor:
                campaign = await cursor.fetchone()
            if campaign is None:
                await self._audit(
                    db, "REANALYZE", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    hashes=self._payload_hashes(payload), error_code="TRIAL_CAMPAIGN_NOT_ACTIVE",
                )
                await db.commit()
                raise PromotionRejected("Trial campaign is not active")
            await db.execute(
                "UPDATE nexus_campaigns SET status='CLOSED',ended_at=CURRENT_TIMESTAMP WHERE id=? AND status='ACTIVE'",
                (campaign["id"],),
            )
            new_campaign_id = f"trial-reanalyze-{request_id}-{campaign['nexus_version_id'][-12:]}"
            await db.execute(
                "INSERT INTO nexus_campaigns(id,lane,nexus_version_id,status) VALUES (?,?,?,'ACTIVE')",
                (new_campaign_id, "challenger_trial", campaign["nexus_version_id"]),
            )
            await db.execute(
                "UPDATE nexus_proposals SET status='REANALYZE',revision=revision+1 "
                "WHERE id=? AND revision=? AND status='PENDING_USER_REVIEW'",
                (proposal["id"], proposal["revision"]),
            )
            cas = await db.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND strategy_id='nexus_trade' AND config_revision=?",
                (NEXUS_TRADE_BOT_ID, expected_revision),
            )
            if cas.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("stale NexusTrade revision")
            new_revision = actual + 1
            after = await self._pointers(db)
            await self._audit(
                db, "REANALYZE", actor, reason, request_id,
                expected_revision, new_revision, "COMMITTED", before, after,
                hashes=self._payload_hashes(payload),
            )
            events = [
                ("nexus.proposal", {"id": proposal["id"], "status": "REANALYZE", "revision": proposal["revision"] + 1}),
                ("nexus.campaign", {"id": new_campaign_id, "lane": "challenger_trial", "status": "ACTIVE", "nexus_version_id": campaign["nexus_version_id"], "accumulated_operations": 0}),
                ("nexus.trial_changed", {"lane": "challenger_trial", "campaign": {"id": new_campaign_id, "status": "ACTIVE", "accumulated_operations": 0}, "version_id": campaign["nexus_version_id"]}),
            ]
            await self._write_events(db, "REANALYZE", request_id, new_revision, events)
            result = {
                "action": "REANALYZE", "outcome": "COMMITTED",
                "snapshot_version": new_revision, "before": before, "after": after,
            }
            await self._store_request(db, "REANALYZE", request_id, input_hash, result)
            await db.commit()
            result["events"] = await self._events_for_request("REANALYZE", request_id)
            return result

    async def replace_trial(
        self,
        boundary: datetime,
        *,
        actor: str = "system:scheduler",
        request_id: str,
        reason: str,
        candidate_id: str | None = None,
    ) -> dict:
        actor, request_id, reason = self._validate_identity(actor, request_id, reason)
        if not isinstance(boundary, datetime) or boundary.tzinfo is None:
            raise ValueError("an aware weekly boundary is required")
        local = boundary.astimezone(BRASILIA)
        if (
            local.weekday() != 0 or local.hour != 10 or local.minute != 0
            or local.second != 0 or local.microsecond != 0
        ):
            async with self._connection() as db:
                await db.execute("BEGIN IMMEDIATE")
                actual = await self._actual_revision(db)
                before = await self._pointers(db)
                await self._audit(
                    db, "REPLACE_TRIAL", actor, reason, request_id,
                    actual, actual, "REJECTED", before, before,
                    error_code="NOT_WEEKLY_BOUNDARY",
                )
                await db.commit()
            raise PromotionRejected("Trial replacement requires exact Monday 10:00 America/Sao_Paulo")
        boundary_utc = boundary.astimezone(timezone.utc).isoformat()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            actual = await self._actual_revision(db)
            before = await self._pointers(db)
            async with db.execute(
                "SELECT result_json FROM nexus_trial_boundaries WHERE boundary_utc=?",
                (boundary_utc,),
            ) as cursor:
                processed = await cursor.fetchone()
            if processed is not None:
                stored = json.loads(processed["result_json"])
                result = {**stored, "changed": False, "reason": "ALREADY_PROCESSED", "events": []}
                await self._audit(
                    db, "REPLACE_TRIAL", actor, reason, request_id,
                    actual, actual, "IDEMPOTENT", before, before,
                    error_code="BOUNDARY_ALREADY_PROCESSED",
                )
                await db.commit()
                return result
            async with db.execute(
                "SELECT 1 FROM nexus_proposals WHERE status='PENDING_USER_REVIEW' LIMIT 1"
            ) as cursor:
                pending = await cursor.fetchone() is not None
            if pending:
                result = {
                    "action": "REPLACE_TRIAL", "outcome": "RETAINED", "changed": False,
                    "reason": "PENDING_PROPOSAL", "snapshot_version": actual,
                    "before": before, "after": before, "events": [],
                }
                await self._retain_trial_boundary(
                    db, boundary_utc, request_id, result, actor, reason, actual, before,
                    "PENDING_PROPOSAL",
                )
                await db.commit()
                return result
            candidate = await self._qualified_shadow(db, candidate_id)
            if candidate is None:
                result = {
                    "action": "REPLACE_TRIAL", "outcome": "RETAINED", "changed": False,
                    "reason": "NO_QUALIFIED_CANDIDATE", "snapshot_version": actual,
                    "before": before, "after": before, "events": [],
                }
                await self._retain_trial_boundary(
                    db, boundary_utc, request_id, result, actor, reason, actual, before,
                    "NO_QUALIFIED_CANDIDATE",
                )
                await db.commit()
                return result
            async with db.execute(
                "SELECT id FROM nexus_candidates WHERE status='TRIAL'"
            ) as cursor:
                old_candidate = await cursor.fetchone()
            async with db.execute(
                "SELECT id FROM nexus_campaigns WHERE lane='challenger_trial' "
                "AND nexus_version_id=? AND status='ACTIVE'",
                (before["trial_version_id"],),
            ) as cursor:
                old_campaign = await cursor.fetchone()
            if old_candidate is None or old_campaign is None:
                await self._audit(
                    db, "REPLACE_TRIAL", actor, reason, request_id,
                    actual, actual, "REJECTED", before, before,
                    error_code="ACTIVE_TRIAL_IDENTITY_INVALID",
                )
                await db.commit()
                raise PromotionRejected("active Trial candidate/campaign/version identity is invalid")
            try:
                artifact = CandidateArtifact.from_json(candidate["metadata"])
            except (ArtifactIntegrityError, TypeError, ValueError) as exc:
                await self._audit(
                    db, "REPLACE_TRIAL", actor, reason, request_id,
                    actual, actual, "REJECTED", before, before,
                    hashes={"candidate_hash": candidate["artifact_hash"]},
                    error_code="ARTIFACT_CORRUPT",
                )
                await db.commit()
                raise PromotionRejected("qualified SHADOW artifact is corrupt") from exc
            trial_snapshot = {
                "schema_version": 1,
                "candidate_id": candidate["id"],
                "artifact": json.loads(artifact.to_json()),
                "trial_selection": {"boundary_utc": boundary_utc},
            }
            encoded = canonical_json(trial_snapshot)
            version_hash = hashlib.sha256(
                b"nexus-trial-version-v1\0" + encoded.encode("utf-8")
            ).hexdigest()
            version_id = f"nexus-trial-{version_hash[:16]}"
            await db.execute(
                "INSERT INTO nexus_versions(id,name,status,version_hash,snapshot) VALUES (?,?,?,?,?)",
                (version_id, f"Trial {version_hash[:8]}", "TRIAL", version_hash, encoded),
            )
            campaign_update = await db.execute(
                "UPDATE nexus_campaigns SET status='SUPERSEDED',ended_at=? "
                "WHERE id=? AND lane='challenger_trial' AND nexus_version_id=? AND status='ACTIVE'",
                (boundary_utc, old_campaign["id"], before["trial_version_id"]),
            )
            if campaign_update.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("active Trial campaign changed during replacement")
            campaign_id = f"trial-{version_hash[:16]}-{local.date().isoformat()}"
            await db.execute(
                "INSERT INTO nexus_campaigns(id,lane,nexus_version_id,status,started_at) "
                "VALUES (?,?,?,'ACTIVE',?)",
                (campaign_id, "challenger_trial", version_id, boundary_utc),
            )
            transition_id = "trial-role-" + hashlib.sha256(
                canonical_json({
                    "boundary_utc": boundary_utc,
                    "request_id": request_id,
                    "old_candidate_id": old_candidate["id"],
                    "new_candidate_id": candidate["id"],
                    "old_version_id": before["trial_version_id"],
                    "new_version_id": version_id,
                    "old_campaign_id": old_campaign["id"],
                    "new_campaign_id": campaign_id,
                }).encode("utf-8")
            ).hexdigest()[:32]
            await db.execute(
                "INSERT INTO nexus_candidate_role_transitions ("
                "id,boundary_utc,request_id,actor,reason,old_candidate_id,new_candidate_id,"
                "old_version_id,new_version_id,old_campaign_id,new_campaign_id"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transition_id, boundary_utc, request_id, actor, reason,
                    old_candidate["id"], candidate["id"], before["trial_version_id"],
                    version_id, old_campaign["id"], campaign_id,
                ),
            )
            old_role = await db.execute(
                "UPDATE nexus_candidates SET status='SHADOW' WHERE id=? AND status='TRIAL'",
                (old_candidate["id"],),
            )
            new_role = await db.execute(
                "UPDATE nexus_candidates SET status='TRIAL' WHERE id=? AND status='SHADOW'",
                (candidate["id"],),
            )
            if old_role.rowcount != 1 or new_role.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("Trial candidate roles changed during replacement")
            await self._fault_point(
                db, "after_candidate_roles", "REPLACE_TRIAL", actor, reason, request_id,
                actual, actual, before,
            )
            await db.execute(
                "UPDATE nexus_runtime SET trial_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE bot_id=?",
                (version_id, NEXUS_TRADE_BOT_ID),
            )
            cas = await db.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND strategy_id='nexus_trade' AND config_revision=?",
                (NEXUS_TRADE_BOT_ID, actual),
            )
            if cas.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("stale NexusTrade revision")
            new_revision = actual + 1
            after = await self._pointers(db)
            hashes = {
                "candidate_hash": candidate["artifact_hash"],
                "artifact_hash": artifact.artifact_hash,
                "metadata_hash": artifact.metadata_hash,
                "configuration_hash": artifact.metadata["configuration_hash"],
                "version_hash": version_hash,
            }
            await self._audit(
                db, "REPLACE_TRIAL", actor, reason, request_id,
                actual, new_revision, "COMMITTED", before, after, hashes=hashes,
            )
            events = [
                ("nexus.version_changed", {"lane": "challenger_trial", "version": {"id": version_id, "version_hash": version_hash}}),
                ("nexus.campaign", {"id": campaign_id, "lane": "challenger_trial", "status": "ACTIVE", "nexus_version_id": version_id, "accumulated_operations": 0}),
                ("nexus.trial_changed", {"lane": "challenger_trial", "version": {"id": version_id, "version_hash": version_hash}, "campaign": {"id": campaign_id, "status": "ACTIVE", "accumulated_operations": 0}}),
            ]
            await self._write_events(db, "REPLACE_TRIAL", request_id, new_revision, events)
            result = {
                "action": "REPLACE_TRIAL", "outcome": "COMMITTED", "changed": True,
                "reason": "QUALIFIED_SHADOW_SELECTED", "snapshot_version": new_revision,
                "before": before, "after": after,
            }
            await db.execute(
                "INSERT INTO nexus_trial_boundaries(boundary_utc,request_id,outcome,result_json) VALUES (?,?,?,?)",
                (boundary_utc, request_id, "COMMITTED", canonical_json(result)),
            )
            await db.commit()
            result["events"] = await self._events_for_request("REPLACE_TRIAL", request_id)
            return result

    async def rollback(
        self,
        target_version_id: str,
        expected_revision: int,
        actor: str,
        *,
        target_version_hash: str,
        request_id: str,
        reason: str,
    ) -> dict:
        actor, request_id, reason = self._validate_identity(actor, request_id, reason)
        if type(target_version_id) is not str or not target_version_id.strip():
            raise ValueError("target_version_id is required")
        if type(target_version_hash) is not str or len(target_version_hash) != 64:
            raise ValueError("target_version_hash must be a SHA-256 hex digest")
        if isinstance(expected_revision, bool) or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            actual = await self._actual_revision(db)
            before = await self._pointers(db)
            input_hash = self._request_input_hash(
                "ROLLBACK",
                {
                    "target_version_id": target_version_id.strip(),
                    "target_version_hash": target_version_hash,
                    "expected_revision": expected_revision,
                    "actor": actor,
                    "reason": reason,
                },
            )
            replay = await self._load_request(db, "ROLLBACK", request_id)
            if replay is not None:
                if replay["input_hash"] != input_hash:
                    await self._audit(
                        db, "ROLLBACK", actor, reason, request_id,
                        expected_revision, actual, "CONFLICTED", before, before,
                        hashes={"target_version_hash": target_version_hash},
                        error_code="REQUEST_ID_REUSED",
                    )
                    await db.commit()
                    raise PromotionConflict("request_id is already bound to different input")
                result = json.loads(replay["result_json"])
                await self._audit(
                    db, "ROLLBACK", actor, reason, request_id,
                    expected_revision, actual, "IDEMPOTENT", before, before,
                    hashes={"target_version_hash": target_version_hash},
                    error_code="REQUEST_REPLAY",
                )
                await db.commit()
                result["idempotent"] = True
                result["events"] = await self._events_for_request("ROLLBACK", request_id)
                return result
            if actual != expected_revision:
                await self._audit(
                    db, "ROLLBACK", actor, reason, request_id,
                    expected_revision, actual, "CONFLICTED", before, before,
                    hashes={"target_version_hash": target_version_hash},
                    error_code="STALE_REVISION",
                )
                await db.commit()
                raise PromotionConflict("stale NexusTrade revision")
            async with db.execute(
                "SELECT * FROM nexus_runtime WHERE bot_id=?", (NEXUS_TRADE_BOT_ID,)
            ) as cursor:
                runtime = await cursor.fetchone()
            rejection = None
            if runtime is None or runtime["champion_enabled"] != 0:
                rejection = "CHAMPION_MUST_BE_OFF"
            elif runtime["champion_version_id"] == target_version_id:
                rejection = "TARGET_ALREADY_CHAMPION"
            target = None
            if rejection is None:
                async with db.execute(
                    "SELECT * FROM nexus_versions WHERE id=?", (target_version_id.strip(),)
                ) as cursor:
                    target = await cursor.fetchone()
                if target is None or target["status"] != "CHAMPION":
                    rejection = "ROLLBACK_TARGET_INVALID"
                elif target["version_hash"] != target_version_hash:
                    rejection = "TARGET_HASH_MISMATCH"
            if rejection is None:
                try:
                    target_snapshot = json.loads(target["snapshot"])
                    encoded = canonical_json(target_snapshot)
                except (TypeError, json.JSONDecodeError, ValueError) as exc:
                    rejection = "TARGET_ARTIFACT_CORRUPT"
                else:
                    valid_hashes = {
                        hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                        hashlib.sha256(b"nexus-champion-version-v1\0" + encoded.encode("utf-8")).hexdigest(),
                    }
                    if target["snapshot"] != encoded or target["version_hash"] not in valid_hashes:
                        rejection = "TARGET_ARTIFACT_CORRUPT"
            if rejection is None:
                try:
                    await self._assert_champion_lane_safe(db)
                except PromotionRejected as exc:
                    rejection = str(exc)
            if rejection is not None:
                await self._audit(
                    db, "ROLLBACK", actor, reason, request_id,
                    expected_revision, actual, "REJECTED", before, before,
                    hashes={"target_version_hash": target_version_hash}, error_code=rejection,
                )
                await db.commit()
                raise PromotionRejected(rejection)
            await db.execute(
                "UPDATE nexus_campaigns SET status='CLOSED',ended_at=CURRENT_TIMESTAMP "
                "WHERE lane='champion_baseline' AND status='ACTIVE'"
            )
            campaign_id = f"champion-rollback-{request_id}-{target_version_id[-12:]}"
            await db.execute(
                "INSERT INTO nexus_campaigns(id,lane,nexus_version_id,status) VALUES (?,?,?,'ACTIVE')",
                (campaign_id, "champion_baseline", target_version_id),
            )
            await db.execute(
                "UPDATE nexus_runtime SET champion_version_id=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE bot_id=? AND champion_enabled=0",
                (target_version_id, NEXUS_TRADE_BOT_ID),
            )
            await self._fault_point(
                db, "after_pointer", "ROLLBACK", actor, reason, request_id,
                expected_revision, actual, before,
            )
            cas = await db.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND strategy_id='nexus_trade' AND config_revision=?",
                (NEXUS_TRADE_BOT_ID, expected_revision),
            )
            if cas.rowcount != 1:
                await db.rollback()
                raise PromotionConflict("stale NexusTrade revision")
            new_revision = actual + 1
            after = await self._pointers(db)
            await self._audit(
                db, "ROLLBACK", actor, reason, request_id,
                expected_revision, new_revision, "COMMITTED", before, after,
                hashes={"target_version_hash": target_version_hash},
            )
            events = [
                ("nexus.version_changed", {"lane": "champion_baseline", "version": {"id": target_version_id, "version_hash": target_version_hash}, "transition": "ROLLBACK"}),
                ("nexus.campaign", {"id": campaign_id, "lane": "champion_baseline", "status": "ACTIVE", "nexus_version_id": target_version_id}),
            ]
            await self._write_events(db, "ROLLBACK", request_id, new_revision, events)
            result = {
                "action": "ROLLBACK", "outcome": "COMMITTED",
                "snapshot_version": new_revision, "before": before, "after": after,
            }
            await self._store_request(db, "ROLLBACK", request_id, input_hash, result)
            await db.commit()
            result["events"] = await self._events_for_request("ROLLBACK", request_id)
            return result

    async def _retain_trial_boundary(
        self, db, boundary_utc, request_id, result, actor, reason, revision, before, code,
    ) -> None:
        await self._audit(
            db, "REPLACE_TRIAL", actor, reason, request_id,
            revision, revision, "RETAINED", before, before, error_code=code,
        )
        await db.execute(
            "INSERT INTO nexus_trial_boundaries(boundary_utc,request_id,outcome,result_json) VALUES (?,?,?,?)",
            (boundary_utc, request_id, "RETAINED", canonical_json(result)),
        )

    @staticmethod
    async def _qualified_shadow(db, candidate_id: str | None):
        query = "SELECT * FROM nexus_candidates WHERE status='SHADOW'"
        params = ()
        if candidate_id is not None:
            query += " AND id=?"
            params = (candidate_id,)
        query += " ORDER BY created_at,id"
        async with db.execute(query, params) as cursor:
            candidates = await cursor.fetchall()
        for candidate in candidates:
            try:
                CandidateArtifact.from_json(candidate["metadata"]).executable_gate()
            except (ArtifactIntegrityError, TypeError, ValueError):
                continue
            async with db.execute(
                "SELECT payload FROM nexus_training_attempts WHERE status='SUCCEEDED' "
                "AND payload LIKE ? ORDER BY created_at DESC,id DESC",
                (f'%"candidate_id":"{candidate["id"]}"%',),
            ) as cursor:
                attempts = await cursor.fetchall()
            for attempt in attempts:
                try:
                    payload = json.loads(attempt["payload"])
                except (TypeError, json.JSONDecodeError):
                    continue
                qualification = payload.get("qualification")
                gates = qualification.get("gates") if isinstance(qualification, dict) else None
                if (
                    payload.get("artifact_hash") == candidate["artifact_hash"]
                    and isinstance(qualification, dict)
                    and qualification.get("status") == "PASS"
                    and isinstance(gates, list) and gates
                    and all(isinstance(gate, dict) and gate.get("status") == "PASS" for gate in gates)
                ):
                    return candidate
        return None

    @staticmethod
    def _payload_hashes(payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {}
        return {
            key: payload[key]
            for key in (
                "report_hash", "candidate_hash", "artifact_hash", "metadata_hash",
                "configuration_hash", "dataset_hash", "provenance_hash",
                "trial_version_hash",
            )
            if key in payload
        }

    @staticmethod
    def _request_input_hash(action: str, payload: dict) -> str:
        return hashlib.sha256(
            (action + "\0" + canonical_json(payload)).encode("utf-8")
        ).hexdigest()

    @staticmethod
    async def _load_request(db, action: str, request_id: str):
        async with db.execute(
            "SELECT * FROM nexus_transition_requests WHERE action=? AND request_id=?",
            (action, request_id),
        ) as cursor:
            return await cursor.fetchone()

    @staticmethod
    async def _store_request(db, action: str, request_id: str, input_hash: str, result: dict):
        await db.execute(
            "INSERT INTO nexus_transition_requests(action,request_id,input_hash,outcome,result_json) "
            "VALUES (?,?,?,?,?)",
            (action, request_id, input_hash, result["outcome"], canonical_json(result)),
        )

    async def _validate_approval(
        self,
        db: aiosqlite.Connection,
        proposal: aiosqlite.Row,
        *,
        reinforced_confirmation: bool,
    ) -> dict:
        if proposal["status"] != "PENDING_USER_REVIEW":
            raise PromotionRejected("PROPOSAL_NOT_PENDING")
        try:
            payload = json.loads(proposal["payload"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PromotionRejected("PROPOSAL_CORRUPT") from exc
        required = {
            "report_id", "report_hash", "candidate_id", "candidate_hash",
            "artifact_hash", "metadata_hash", "configuration_hash", "dataset_hash",
            "provenance_hash", "trial_version_id", "trial_version_hash", "campaign_id",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise PromotionRejected("PROPOSAL_HASH_BINDINGS_INCOMPLETE")
        async with db.execute(
            "SELECT * FROM nexus_runtime WHERE bot_id=?", (NEXUS_TRADE_BOT_ID,)
        ) as cursor:
            runtime = await cursor.fetchone()
        if runtime is None or runtime["champion_enabled"] != 0:
            raise PromotionRejected("CHAMPION_MUST_BE_OFF")
        await self._assert_champion_lane_safe(db)
        async with db.execute("SELECT * FROM nexus_reports WHERE id=?", (payload["report_id"],)) as cursor:
            report = await cursor.fetchone()
        if report is None or report["report_type"] != "weekly":
            raise PromotionRejected("WEEKLY_REPORT_NOT_FOUND")
        try:
            report_snapshot = json.loads(report["snapshot"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PromotionRejected("REPORT_CORRUPT") from exc
        actual_report_hash = hashlib.sha256(
            b"nexus-report-json-v1\0" + canonical_json(report_snapshot).encode("utf-8")
        ).hexdigest()
        if report["report_hash"] != actual_report_hash or payload["report_hash"] != actual_report_hash:
            raise PromotionRejected("REPORT_HASH_MISMATCH")
        if report["campaign_id"] != proposal["campaign_id"] or payload["campaign_id"] != proposal["campaign_id"]:
            raise PromotionRejected("CAMPAIGN_MISMATCH")
        async with db.execute(
            "SELECT * FROM nexus_campaigns WHERE id=? AND lane='challenger_trial' AND status='ACTIVE'",
            (proposal["campaign_id"],),
        ) as cursor:
            campaign = await cursor.fetchone()
        if campaign is None or campaign["nexus_version_id"] != runtime["trial_version_id"]:
            raise PromotionRejected("TRIAL_CAMPAIGN_NOT_ACTIVE")
        async with db.execute("SELECT * FROM nexus_candidates WHERE id=?", (payload["candidate_id"],)) as cursor:
            candidate = await cursor.fetchone()
        if candidate is None or candidate["status"] != "TRIAL":
            raise PromotionRejected("CANDIDATE_NOT_FROZEN_TRIAL")
        try:
            artifact = CandidateArtifact.from_json(candidate["metadata"])
        except (ArtifactIntegrityError, TypeError, ValueError) as exc:
            raise PromotionRejected("ARTIFACT_CORRUPT") from exc
        trial_report = report_snapshot.get("trial")
        exact = {
            "candidate_hash": candidate["artifact_hash"],
            "artifact_hash": artifact.artifact_hash,
            "metadata_hash": artifact.metadata_hash,
            "configuration_hash": artifact.metadata["configuration_hash"],
            "dataset_hash": artifact.metadata["dataset_hash"],
            "provenance_hash": artifact.metadata["provenance_hash"],
            "trial_version_id": runtime["trial_version_id"],
        }
        async with db.execute("SELECT version_hash FROM nexus_versions WHERE id=?", (runtime["trial_version_id"],)) as cursor:
            trial_version = await cursor.fetchone()
        if trial_version is None:
            raise PromotionRejected("TRIAL_VERSION_MISSING")
        exact["trial_version_hash"] = trial_version["version_hash"]
        if any(payload.get(key) != value for key, value in exact.items()):
            raise PromotionRejected("PROPOSAL_CANDIDATE_HASH_MISMATCH")
        report_exact = {
            "artifact_hash": exact["artifact_hash"],
            "metadata_hash": exact["metadata_hash"],
            "configuration_hash": exact["configuration_hash"],
            "dataset_hash": exact["dataset_hash"],
            "provenance_hash": exact["provenance_hash"],
            "version_id": exact["trial_version_id"],
            "version_hash": exact["trial_version_hash"],
        }
        if not isinstance(trial_report, dict) or any(
            trial_report.get(key) != value for key, value in report_exact.items()
        ):
            raise PromotionRejected("REPORT_CANDIDATE_HASH_MISMATCH")
        progress = report_snapshot.get("accumulated_progress")
        if (
            not isinstance(progress, dict)
            or type(progress.get("operations")) is not int
            or progress["operations"] < 300
            or progress.get("target") != 300
        ):
            raise PromotionRejected("INSUFFICIENT_SAMPLE")
        if report_snapshot.get("complete_days", 0) < 7:
            raise PromotionRejected("INSUFFICIENT_SEVEN_DAY_WINDOW")
        try:
            start = datetime.fromisoformat(report_snapshot["window"]["start_utc"])
            end = datetime.fromisoformat(report_snapshot["window"]["end_utc"])
            window = BrasiliaSchedule().weekly_window(end)
        except (KeyError, TypeError, ValueError) as exc:
            raise PromotionRejected("REPORT_WINDOW_INVALID") from exc
        if start != window.start_utc or end != window.end_utc:
            raise PromotionRejected("REPORT_WINDOW_INVALID")
        gates = report_snapshot.get("gates")
        if not isinstance(gates, list) or not gates:
            raise PromotionRejected("GATES_MISSING")
        if (
            len(gates) != len(_PROMOTION_GATE_CODES)
            or any(
                not isinstance(gate, dict)
                or set(gate) != {"code", "status", "observed", "threshold", "reason"}
                or gate.get("status") not in {"PASS", "FAIL", "INCONCLUSIVE"}
                for gate in gates
            )
            or {gate["code"] for gate in gates} != _PROMOTION_GATE_CODES
        ):
            raise PromotionRejected("GATE_MANIFEST_INVALID")
        gate_map = {gate.get("code"): gate.get("status") for gate in gates if isinstance(gate, dict)}
        hard = {"MINIMUM_SAMPLE", "DATA_INTEGRITY", "COMPARABLE_PROVENANCE", "RISK_LIMITS", "CHANGE_BUDGET"}
        if any(gate_map.get(code) != "PASS" for code in hard):
            raise PromotionRejected("HARD_GATE_FAILED")
        non_passing = [code for code, status in gate_map.items() if status != "PASS"]
        recommendation = report_snapshot.get("recommendation")
        if recommendation == "REANALYZE" and reinforced_confirmation is not True:
            raise PromotionRejected("REANALYZE_CONFIRMATION_REQUIRED")
        if non_passing and not (recommendation == "REANALYZE" and reinforced_confirmation is True):
            raise PromotionRejected("SOFT_GATE_CONFIRMATION_REQUIRED")
        if recommendation not in {"EVOLVE", "RECOMMEND_EVOLUTION", "REANALYZE"}:
            raise PromotionRejected("RECOMMENDATION_NOT_PROMOTABLE")
        return {"runtime": runtime, "candidate": candidate, "artifact": artifact, "report": report}

    @staticmethod
    async def _assert_champion_lane_safe(db: aiosqlite.Connection) -> None:
        unsafe_states = {"RESERVED", "SUBMITTING", "RECONCILE_PENDING", "QUARANTINED", "ACTIVE"}
        async with db.execute(
            """SELECT decision.payload FROM nexus_lane_heads AS head
               JOIN nexus_decisions AS decision ON decision.id=head.snapshot_id
               WHERE head.lane='champion_baseline'"""
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            try:
                lane = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise PromotionRejected("CHAMPION_LANE_CORRUPT") from exc
            if not isinstance(lane, dict) or not isinstance(lane.get("state"), dict):
                raise PromotionRejected("CHAMPION_LANE_CORRUPT")
            state = lane["state"]
            position_status = state.get("position_status")
            known_states = unsafe_states | {"IDLE"}
            if type(position_status) is not str or position_status not in known_states:
                raise PromotionRejected("CHAMPION_LANE_CORRUPT")
            owner_fields = (
                lane.get("owner"),
                state.get("owner_decision_id"),
                state.get("contract_id"),
                state.get("quarantine_correlation_id"),
            )
            if position_status in unsafe_states or any(value is not None for value in owner_fields):
                raise PromotionRejected("CHAMPION_LANE_UNSAFE")
        async with db.execute(
            """SELECT 1 FROM order_intents WHERE bot_id=? AND lane='champion_baseline'
               AND lower(state) IN ('prepared','reserved','submitting','reconcile_pending','ambiguous','quarantined','owned','active') LIMIT 1""",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            if await cursor.fetchone() is not None:
                raise PromotionRejected("CHAMPION_INTENT_PENDING")
        async with db.execute(
            """SELECT 1 FROM trades WHERE bot_id=? AND lane='champion_baseline'
               AND lower(status) NOT IN ('closed','settled','sold','cancelled','rejected') LIMIT 1""",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            if await cursor.fetchone() is not None:
                raise PromotionRejected("CHAMPION_CONTRACT_OPEN")

    @staticmethod
    async def _write_events(db, action: str, request_id: str, revision: int, events: list) -> None:
        for index, (event_type, payload) in enumerate(events):
            event_id = f"{event_type}:{action.lower()}:{request_id}:{revision}:{index}"
            await db.execute(
                "INSERT INTO nexus_event_outbox "
                "(event_id,action,request_id,event_type,snapshot_version,payload) "
                "VALUES (?,?,?,?,?,?)",
                (event_id, action, request_id, event_type, revision, canonical_json(payload)),
            )

    async def _events_for_request(self, action: str, request_id: str) -> list[dict]:
        async with self._connection() as db:
            async with db.execute(
                "SELECT * FROM nexus_event_outbox WHERE action=? AND request_id=? ORDER BY rowid",
                (action, request_id),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "event_id": row["event_id"],
                "schema_version": 1,
                "snapshot_version": row["snapshot_version"],
                "type": row["event_type"],
                "bot_id": NEXUS_TRADE_BOT_ID,
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    async def _fault_point(
        self,
        db: aiosqlite.Connection,
        phase: str,
        action: str,
        actor: str,
        reason: str,
        request_id: str,
        expected_revision: int,
        actual_revision: int,
        before: dict,
    ) -> None:
        if not callable(self.failure_injector):
            return
        try:
            self.failure_injector(phase)
        except BaseException:
            await db.rollback()
            await self._record_fault(
                action, actor, reason, request_id, expected_revision,
                actual_revision, before,
            )
            raise

    async def _record_fault(
        self,
        action: str,
        actor: str,
        reason: str,
        request_id: str,
        expected_revision: int,
        actual_revision: int,
        before: dict,
    ) -> None:
        async with self._connection() as audit_db:
            await audit_db.execute("BEGIN IMMEDIATE")
            await self._audit(
                audit_db, action, actor, reason, request_id,
                expected_revision, actual_revision, "FAULTED", before, before,
                error_code="TRANSITION_FAULT",
            )
            await audit_db.commit()

    @staticmethod
    def _validate_identity(actor: str, request_id: str, reason: str) -> tuple[str, str, str]:
        values = []
        for name, value in (("actor", actor), ("request_id", request_id), ("reason", reason)):
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} is required")
            normalized = value.strip()
            if len(normalized) > 512:
                raise ValueError(f"{name} is too long")
            values.append(normalized)
        validate_safe_json({"actor": values[0], "request_id": values[1], "reason": values[2]})
        return tuple(values)

    @staticmethod
    async def _actual_revision(db: aiosqlite.Connection) -> int:
        async with db.execute(
            "SELECT config_revision FROM bot_instances WHERE id=? AND strategy_id='nexus_trade'",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise PromotionRejected("NexusTrade singleton is unavailable")
        return int(row["config_revision"])

    @staticmethod
    async def _pointers(db: aiosqlite.Connection) -> dict:
        async with db.execute(
            "SELECT champion_version_id, trial_version_id FROM nexus_runtime WHERE bot_id=?",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            runtime = await cursor.fetchone()
        async with db.execute(
            "SELECT id FROM nexus_campaigns WHERE status='ACTIVE' ORDER BY lane, id"
        ) as cursor:
            campaigns = [row["id"] for row in await cursor.fetchall()]
        return {
            "champion_version_id": None if runtime is None else runtime["champion_version_id"],
            "trial_version_id": None if runtime is None else runtime["trial_version_id"],
            "campaign_ids": campaigns,
        }

    @staticmethod
    async def _audit(
        db: aiosqlite.Connection,
        action: str,
        actor: str,
        reason: str,
        request_id: str,
        expected_revision: int,
        actual_revision: int,
        outcome: str,
        before: dict,
        after: dict,
        *,
        hashes: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        material = {
            "action": action,
            "actor": actor,
            "request_id": request_id,
            "expected_revision": expected_revision,
            "actual_revision": actual_revision,
            "outcome": outcome,
        }
        audit_id = "audit-" + hashlib.sha256(
            (canonical_json(material) + uuid.uuid4().hex).encode("utf-8")
        ).hexdigest()[:32]
        await db.execute(
            """
            INSERT INTO nexus_audit_events (
                id, actor, action, reason, request_id, expected_revision,
                actual_revision, outcome, before_json, after_json,
                hashes_json, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id, actor, action, reason, request_id, expected_revision,
                actual_revision, outcome, canonical_json(before), canonical_json(after),
                canonical_json(hashes or {}), error_code,
            ),
        )


__all__ = [
    "PromotionConflict",
    "PromotionError",
    "PromotionRejected",
    "PromotionService",
]
