import asyncio
import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.live_store import LiveStore
from config.settings import settings
from database.repository import DatabaseRepository
from nexus_trade.artifacts import canonical_json
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.promotion import PromotionConflict, PromotionRejected, PromotionService
from tests.test_nexus_trade_learning import ArtifactAndRegistryTests


class PromotionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "promotion.db")
        self.repository = DatabaseRepository(self.db_path)
        asyncio.run(self.repository.init_db())
        self.service = PromotionService(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def snapshot(self):
        return asyncio.run(self.repository.get_nexus_control_snapshot())

    def seed_valid_proposal(self):
        registry = CandidateRegistry(self.db_path)
        artifact = ArtifactAndRegistryTests.artifact("trial-current")
        candidate = registry.register(artifact)
        registry.register(ArtifactAndRegistryTests.artifact("shadow-qualified"))
        before = self.snapshot()
        campaign_id = before["active_campaigns"][0]["id"]
        trial_version = next(
            item["version"] for item in before["lanes"]
            if item["lane"] == "challenger_trial"
        )
        report_snapshot = {
            "schema_version": 1,
            "report_type": "weekly",
            "campaign_id": campaign_id,
            "window": {
                "start_utc": "2026-08-03T13:00:00+00:00",
                "end_utc": "2026-08-10T13:00:00+00:00",
            },
            "complete_days": 7,
            "accumulated_progress": {"operations": 300, "target": 300},
            "recommendation": "EVOLVE",
            "gates": [
                {"code": code, "status": "PASS", "observed": True,
                 "threshold": True, "reason": "verified"}
                for code in (
                    "MINIMUM_SAMPLE", "DATA_INTEGRITY", "COMPARABLE_PROVENANCE",
                    "TRIAL_EXPECTANCY_POSITIVE", "PROFIT_FACTOR",
                    "EXPECTANCY_IMPROVEMENT", "BLOCK_BOOTSTRAP_95", "DRAWDOWN",
                    "RECOVERY", "WORST_ROLLING_50", "LOSS_STREAK", "RISK_LIMITS",
                    "DAILY_STABILITY", "RECENT_STABILITY", "REGIME_STABILITY",
                    "DSR", "PBO", "SENSITIVITY", "CHANGE_BUDGET",
                )
            ],
            "trial": {
                "version_id": trial_version["id"],
                "version_hash": trial_version["version_hash"],
                "artifact_hash": artifact.artifact_hash,
                "metadata_hash": artifact.metadata_hash,
                "configuration_hash": artifact.metadata["configuration_hash"],
                "dataset_hash": artifact.metadata["dataset_hash"],
                "provenance_hash": artifact.metadata["provenance_hash"],
            },
        }
        report_encoded = canonical_json(report_snapshot)
        import hashlib
        report_hash = hashlib.sha256(
            b"nexus-report-json-v1\0" + report_encoded.encode("utf-8")
        ).hexdigest()
        report_id = f"report-{report_hash[:24]}"
        proposal_id = f"proposal-{report_hash[:24]}"
        proposal_payload = {
            "report_id": report_id,
            "report_hash": report_hash,
            "candidate_id": candidate["id"],
            "candidate_hash": artifact.artifact_hash,
            "artifact_hash": artifact.artifact_hash,
            "metadata_hash": artifact.metadata_hash,
            "configuration_hash": artifact.metadata["configuration_hash"],
            "dataset_hash": artifact.metadata["dataset_hash"],
            "provenance_hash": artifact.metadata["provenance_hash"],
            "trial_version_id": trial_version["id"],
            "trial_version_hash": trial_version["version_hash"],
            "campaign_id": campaign_id,
        }
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "INSERT INTO nexus_reports "
                "(id,campaign_id,report_hash,snapshot,report_type,window_start_utc,window_end_utc) "
                "VALUES (?,?,?,?,?,?,?)",
                (report_id, campaign_id, report_hash, report_encoded, "weekly",
                 "2026-08-03T13:00:00+00:00", "2026-08-10T13:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO nexus_proposals "
                "(id,campaign_id,nexus_version_id,revision,status,payload) "
                "VALUES (?,?,?,?,?,?)",
                (proposal_id, campaign_id, trial_version["id"], 1,
                 "PENDING_USER_REVIEW", canonical_json(proposal_payload)),
            )
            db.commit()
        return proposal_id, candidate, artifact, before

    def seed_qualified_shadow(self):
        registry = CandidateRegistry(self.db_path)
        registry.register(ArtifactAndRegistryTests.artifact("trial-current"))
        artifact = ArtifactAndRegistryTests.artifact("qualified-shadow")
        candidate = registry.register(artifact)
        qualification = {
            "candidate_id": candidate["id"],
            "artifact_hash": artifact.artifact_hash,
            "qualification": {
                "status": "PASS",
                "gates": [
                    {"code": "SHADOW_FORWARD", "status": "PASS"},
                    {"code": "ARTIFACT_INTEGRITY", "status": "PASS"},
                ],
            },
        }
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "INSERT INTO nexus_training_attempts "
                "(attempt_hash,status,dataset_hash,provenance_hash,seed,payload) "
                "VALUES (?,'SUCCEEDED',?,?,?,?)",
                ("f" * 64, artifact.metadata["dataset_hash"],
                 artifact.metadata["provenance_hash"], 73, canonical_json(qualification)),
            )
            db.commit()
        return candidate, artifact

    def test_approve_requires_champion_off_and_cas_revision(self):
        before = self.snapshot()
        revision = before["snapshot_version"]

        with self.assertRaises(PromotionRejected):
            asyncio.run(
                self.service.approve(
                    "missing-proposal",
                    revision,
                    "human:operator",
                    request_id="approve-on",
                    reason="reviewed weekly evidence",
                )
            )

        asyncio.run(
            self.repository.set_nexus_champion_mode(
                enabled=True,
                account_id="DEMO-ONLY",
                account_type="demo",
            )
        )
        with self.assertRaises(PromotionConflict):
            asyncio.run(
                self.service.approve(
                    "missing-proposal",
                    revision,
                    "human:operator",
                    request_id="approve-stale",
                    reason="reviewed weekly evidence",
                )
            )

        after = self.snapshot()
        self.assertEqual(
            after["runtime"]["champion_version_id"],
            before["runtime"]["champion_version_id"],
        )
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            audits = db.execute(
                "SELECT outcome, actor, reason, request_id FROM nexus_audit_events "
                "WHERE action='APPROVE' ORDER BY created_at, id"
            ).fetchall()
        self.assertCountEqual([row[0] for row in audits], ["REJECTED", "CONFLICTED"])
        self.assertTrue(all(row[1] == "human:operator" for row in audits))
        self.assertTrue(all(row[2] == "reviewed weekly evidence" for row in audits))

    def test_approve_creates_immutable_champion_and_post_commit_outbox(self):
        proposal_id, candidate, artifact, before = self.seed_valid_proposal()
        old_id = before["runtime"]["champion_version_id"]
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            old_row = db.execute(
                "SELECT * FROM nexus_versions WHERE id=?", (old_id,)
            ).fetchone()

        result = asyncio.run(
            self.service.approve(
                proposal_id,
                before["snapshot_version"],
                "human:operator",
                request_id="approve-valid",
                reason="all governed evidence reviewed",
            )
        )

        after = self.snapshot()
        new_id = after["runtime"]["champion_version_id"]
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(result["snapshot_version"], before["snapshot_version"] + 1)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            preserved = db.execute(
                "SELECT * FROM nexus_versions WHERE id=?", (old_id,)
            ).fetchone()
            promoted = db.execute(
                "SELECT status,version_hash,snapshot FROM nexus_versions WHERE id=?",
                (new_id,),
            ).fetchone()
            proposal = db.execute(
                "SELECT status FROM nexus_proposals WHERE id=?", (proposal_id,)
            ).fetchone()[0]
            audit = db.execute(
                "SELECT outcome,before_json,after_json,hashes_json FROM nexus_audit_events "
                "WHERE request_id='approve-valid'"
            ).fetchone()
            outbox = db.execute(
                "SELECT event_type,event_id,snapshot_version FROM nexus_event_outbox "
                "ORDER BY event_type"
            ).fetchall()
        self.assertEqual(tuple(preserved), tuple(old_row))
        self.assertEqual(promoted[0], "CHAMPION")
        promoted_snapshot = json.loads(promoted[2])
        self.assertEqual(promoted_snapshot["artifact"], json.loads(artifact.to_json()))
        self.assertEqual(promoted_snapshot["candidate_id"], candidate["id"])
        self.assertEqual(proposal, "APPROVED")
        self.assertEqual(audit[0], "COMMITTED")
        self.assertEqual(json.loads(audit[1])["champion_version_id"], old_id)
        self.assertEqual(json.loads(audit[2])["champion_version_id"], new_id)
        self.assertEqual(json.loads(audit[3])["artifact_hash"], artifact.artifact_hash)
        self.assertEqual(
            {row[0] for row in outbox},
            {"nexus.proposal", "nexus.version_changed", "nexus.campaign"},
        )
        self.assertTrue(all(row[1].startswith(f"nexus.") for row in outbox))
        self.assertTrue(all(row[2] == result["snapshot_version"] for row in outbox))

    def test_approve_revalidates_every_champion_ownership_barrier(self):
        proposal_id, _, _, before = self.seed_valid_proposal()
        version_id = before["runtime"]["champion_version_id"]
        campaign_id = next(
            row["id"] for row in self.snapshot()["active_campaigns"]
            if row["lane"] == "challenger_trial"
        )
        unsafe = ("RESERVED", "SUBMITTING", "RECONCILE_PENDING", "QUARANTINED", "ACTIVE")
        for index, state in enumerate(unsafe):
            with self.subTest(state=state):
                decision_id = f"unsafe-{index}"
                with contextlib.closing(sqlite3.connect(self.db_path)) as db:
                    db.execute(
                        "INSERT INTO nexus_decisions "
                        "(id,lane,nexus_version_id,campaign_id,symbol,signal_epoch,payload) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (decision_id, "champion_baseline", version_id, campaign_id,
                         "R_100", 100 + index, json.dumps({"state": state})),
                    )
                    db.execute(
                        "INSERT INTO nexus_lane_heads(lane,snapshot_id) VALUES (?,?) "
                        "ON CONFLICT(lane) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                        ("champion_baseline", decision_id),
                    )
                    db.commit()
                with self.assertRaisesRegex(PromotionRejected, "LANE"):
                    asyncio.run(
                        self.service.approve(
                            proposal_id, before["snapshot_version"], "human:operator",
                            request_id=f"unsafe-{state.lower()}", reason="safety check",
                        )
                    )
                with contextlib.closing(sqlite3.connect(self.db_path)) as db:
                    db.execute("DELETE FROM nexus_lane_heads WHERE lane='champion_baseline'")
                    db.execute("DELETE FROM nexus_decisions WHERE id=?", (decision_id,))
                    db.commit()

        for state in ("prepared", "submitting", "reconcile_pending", "ambiguous", "owned"):
            with self.subTest(intent_state=state):
                with contextlib.closing(sqlite3.connect(self.db_path)) as db:
                    db.execute(
                        "INSERT INTO order_intents "
                        "(id,bot_id,account_id,proposal_id,symbol,contract_type,stake,price,"
                        "duration,duration_unit,state,lane,nexus_version_id,campaign_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"intent-{state}", "nexus-trade", f"demo-{state}", "deriv-proposal",
                         "R_100", "CALL", .35, .35, 58, "s", state,
                         "champion_baseline", version_id, campaign_id),
                    )
                    db.commit()
                with self.assertRaisesRegex(PromotionRejected, "INTENT"):
                    asyncio.run(
                        self.service.approve(
                            proposal_id, before["snapshot_version"], "human:operator",
                            request_id=f"unsafe-intent-{state}", reason="safety check",
                        )
                    )
                with contextlib.closing(sqlite3.connect(self.db_path)) as db:
                    db.execute("DELETE FROM order_intents WHERE id=?", (f"intent-{state}",))
                    db.commit()

        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "INSERT INTO trades(bot_id,symbol,contract_id,status,lane,nexus_version_id,campaign_id) "
                "VALUES ('nexus-trade','R_100',999,'open','champion_baseline',?,?)",
                (version_id, campaign_id),
            )
            db.commit()
        with self.assertRaisesRegex(PromotionRejected, "CONTRACT"):
            asyncio.run(
                self.service.approve(
                    proposal_id, before["snapshot_version"], "human:operator",
                    request_id="unsafe-contract", reason="safety check",
                )
            )
        self.assertEqual(
            self.snapshot()["runtime"]["champion_version_id"],
            before["runtime"]["champion_version_id"],
        )

    def test_fault_before_commit_rolls_back_pointer_and_records_fault_without_success_event(self):
        proposal_id, _, _, before = self.seed_valid_proposal()

        def inject(phase):
            if phase == "after_pointer":
                raise RuntimeError("injected transition failure")

        service = PromotionService(self.db_path, failure_injector=inject)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            asyncio.run(
                service.approve(
                    proposal_id, before["snapshot_version"], "human:operator",
                    request_id="approve-fault", reason="fault exercise",
                )
            )

        after = self.snapshot()
        self.assertEqual(after["snapshot_version"], before["snapshot_version"])
        self.assertEqual(after["runtime"], before["runtime"])
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT status FROM nexus_proposals WHERE id=?", (proposal_id,)).fetchone()[0],
                "PENDING_USER_REVIEW",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM nexus_event_outbox").fetchone()[0], 0)
            audit = db.execute(
                "SELECT outcome,error_code FROM nexus_audit_events WHERE request_id='approve-fault'"
            ).fetchone()
        self.assertEqual(audit[0], "FAULTED")
        self.assertEqual(audit[1], "TRANSITION_FAULT")

    def test_concurrent_double_approve_commits_exactly_once(self):
        proposal_id, _, _, before = self.seed_valid_proposal()

        async def approve(request_id):
            try:
                return await PromotionService(self.db_path).approve(
                    proposal_id, before["snapshot_version"], "human:operator",
                    request_id=request_id, reason="concurrency proof",
                )
            except PromotionConflict:
                return {"outcome": "CONFLICTED"}

        async def run_concurrently():
            return await asyncio.gather(approve("concurrent-a"), approve("concurrent-b"))

        results = asyncio.run(run_concurrently())
        self.assertCountEqual([result["outcome"] for result in results], ["COMMITTED", "CONFLICTED"])
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            committed = db.execute(
                "SELECT COUNT(*) FROM nexus_audit_events WHERE action='APPROVE' AND outcome='COMMITTED'"
            ).fetchone()[0]
        self.assertEqual(committed, 1)

    def test_reanalyze_preserves_champion_and_all_evidence_but_starts_zero_campaign(self):
        proposal_id, candidate, artifact, before = self.seed_valid_proposal()
        old_campaign = before["active_campaigns"][0]["id"]
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            immutable_before = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "nexus_candidates", "nexus_reports", "nexus_training_attempts",
                    "nexus_candles", "nexus_features", "trades", "nexus_versions",
                )
            }
            report_bytes = db.execute("SELECT snapshot FROM nexus_reports").fetchone()[0]
            candidate_bytes = db.execute(
                "SELECT metadata FROM nexus_candidates WHERE id=?", (candidate["id"],)
            ).fetchone()[0]

        result = asyncio.run(
            self.service.reanalyze(
                proposal_id,
                before["snapshot_version"],
                "human:operator",
                request_id="reanalyze-valid",
                reason="request another governed evaluation cycle",
            )
        )

        after = self.snapshot()
        self.assertEqual(after["runtime"]["champion_version_id"], before["runtime"]["champion_version_id"])
        self.assertEqual(after["runtime"]["trial_version_id"], before["runtime"]["trial_version_id"])
        self.assertEqual(after["snapshot_version"], before["snapshot_version"] + 1)
        active = after["active_campaigns"]
        self.assertEqual(len(active), 1)
        self.assertNotEqual(active[0]["id"], old_campaign)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT status FROM nexus_campaigns WHERE id=?", (old_campaign,)).fetchone()[0],
                "CLOSED",
            )
            self.assertEqual(
                db.execute("SELECT status FROM nexus_proposals WHERE id=?", (proposal_id,)).fetchone()[0],
                "REANALYZE",
            )
            immutable_after = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in immutable_before
            }
            self.assertEqual(db.execute("SELECT snapshot FROM nexus_reports").fetchone()[0], report_bytes)
            self.assertEqual(
                db.execute("SELECT metadata FROM nexus_candidates WHERE id=?", (candidate["id"],)).fetchone()[0],
                candidate_bytes,
            )
            new_campaign_id = active[0]["id"]
            new_operations = db.execute(
                "SELECT COUNT(*) FROM trades WHERE campaign_id=? AND status='closed'",
                (new_campaign_id,),
            ).fetchone()[0]
        self.assertEqual(immutable_after, immutable_before)
        self.assertEqual(new_operations, 0)
        self.assertEqual(result["outcome"], "COMMITTED")
        self.assertEqual(
            {event["type"] for event in result["events"]},
            {"nexus.proposal", "nexus.campaign", "nexus.trial_changed"},
        )

    def test_weekly_trial_replacement_is_exact_atomic_and_concurrent_idempotent(self):
        candidate, artifact = self.seed_qualified_shadow()
        before = self.snapshot()
        old_campaign = before["active_campaigns"][0]["id"]
        old_trial = before["runtime"]["trial_version_id"]
        old_champion = before["runtime"]["champion_version_id"]
        boundary = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)

        async def replace(request_id):
            return await PromotionService(self.db_path).replace_trial(
                boundary,
                actor="system:scheduler",
                request_id=request_id,
                reason="weekly governed selection",
            )

        async def concurrent():
            return await asyncio.gather(replace("weekly-a"), replace("weekly-b"))

        results = asyncio.run(concurrent())
        after = self.snapshot()
        self.assertEqual(sum(result["changed"] for result in results), 1)
        self.assertEqual(after["runtime"]["champion_version_id"], old_champion)
        self.assertNotEqual(after["runtime"]["trial_version_id"], old_trial)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT status FROM nexus_campaigns WHERE id=?", (old_campaign,)).fetchone()[0],
                "SUPERSEDED",
            )
            new_campaign = db.execute(
                "SELECT id FROM nexus_campaigns WHERE lane='challenger_trial' AND status='ACTIVE'"
            ).fetchone()[0]
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM trades WHERE campaign_id=?", (new_campaign,)).fetchone()[0],
                0,
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM nexus_trial_boundaries").fetchone()[0], 1)
            stored_candidate = db.execute(
                "SELECT status,metadata FROM nexus_candidates WHERE id=?", (candidate["id"],)
            ).fetchone()
        self.assertEqual(stored_candidate[0], "SHADOW")
        self.assertEqual(stored_candidate[1], artifact.to_json())

    def test_trial_replacement_retains_progress_without_candidate_or_with_pending_proposal(self):
        boundary = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
        before = self.snapshot()
        retained = asyncio.run(
            self.service.replace_trial(
                boundary,
                actor="system:scheduler",
                request_id="weekly-no-candidate",
                reason="weekly governed selection",
            )
        )
        self.assertFalse(retained["changed"])
        self.assertEqual(self.snapshot()["runtime"], before["runtime"])

        # A different future boundary with a qualified candidate is frozen by review.
        self.seed_valid_proposal()
        monday = datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc)
        frozen_before = self.snapshot()
        frozen = asyncio.run(
            self.service.replace_trial(
                monday,
                actor="system:scheduler",
                request_id="weekly-pending",
                reason="weekly governed selection",
            )
        )
        self.assertFalse(frozen["changed"])
        self.assertEqual(frozen["reason"], "PENDING_PROPOSAL")
        self.assertEqual(self.snapshot()["runtime"], frozen_before["runtime"])

        with self.assertRaises(PromotionRejected):
            asyncio.run(
                self.service.replace_trial(
                    datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
                    actor="system:scheduler",
                    request_id="weekly-midweek",
                    reason="weekly governed selection",
                )
            )

    def test_explicit_rollback_reuses_valid_history_with_same_safety_and_survives_restart(self):
        proposal_id, _, _, initial = self.seed_valid_proposal()
        old_id = initial["runtime"]["champion_version_id"]
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            old_hash = db.execute(
                "SELECT version_hash FROM nexus_versions WHERE id=?", (old_id,)
            ).fetchone()[0]
        promoted = asyncio.run(
            self.service.approve(
                proposal_id, initial["snapshot_version"], "human:operator",
                request_id="approve-before-rollback", reason="prepare rollback history",
            )
        )
        degraded_id = self.snapshot()["runtime"]["champion_version_id"]
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            degraded_hash = db.execute(
                "SELECT version_hash FROM nexus_versions WHERE id=?", (degraded_id,)
            ).fetchone()[0]

        rolled_back = asyncio.run(
            PromotionService(self.db_path).rollback(
                old_id,
                promoted["snapshot_version"],
                "human:operator",
                target_version_hash=old_hash,
                request_id="rollback-valid",
                reason="degraded forward performance",
            )
        )

        restarted = PromotionService(self.db_path)
        snapshot = asyncio.run(DatabaseRepository(self.db_path).get_nexus_control_snapshot())
        self.assertEqual(snapshot["runtime"]["champion_version_id"], old_id)
        self.assertEqual(rolled_back["snapshot_version"], promoted["snapshot_version"] + 1)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM nexus_versions WHERE id=?", (degraded_id,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM nexus_versions WHERE id=?", (old_id,)).fetchone())
            audit = db.execute(
                "SELECT outcome,before_json,after_json,hashes_json FROM nexus_audit_events "
                "WHERE request_id='rollback-valid'"
            ).fetchone()
        self.assertEqual(audit[0], "COMMITTED")
        self.assertEqual(json.loads(audit[1])["champion_version_id"], degraded_id)
        self.assertEqual(json.loads(audit[2])["champion_version_id"], old_id)
        self.assertEqual(json.loads(audit[3])["target_version_hash"], old_hash)
        self.assertIsInstance(restarted, PromotionService)

        replay = asyncio.run(
            restarted.rollback(
                old_id,
                promoted["snapshot_version"],
                "human:operator",
                target_version_hash=old_hash,
                request_id="rollback-valid",
                reason="degraded forward performance",
            )
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["after"], rolled_back["after"])
        self.assertEqual(self.snapshot()["snapshot_version"], rolled_back["snapshot_version"])

        with self.assertRaises(PromotionConflict):
            asyncio.run(
                restarted.rollback(
                    degraded_id,
                    promoted["snapshot_version"],
                    "human:operator",
                    target_version_hash=degraded_hash,
                    request_id="rollback-stale",
                    reason="stale retry",
                )
            )

    def test_authenticated_routes_are_thin_and_publish_committed_repair_state(self):
        proposal_id, _, _, before = self.seed_valid_proposal()
        live_store = LiveStore()
        with TestClient(
            create_app(self.repository, live_store),
            headers={"X-API-Key": settings.DASHBOARD_API_KEY},
        ) as client:
            denied = client.post(
                f"/api/v1/nexus-trade/proposals/{proposal_id}/approve",
                headers={"X-API-Key": "wrong-key"},
                json={
                    "expected_revision": before["snapshot_version"],
                    "actor": "human:operator",
                    "request_id": "api-denied",
                    "reason": "must authenticate",
                },
            )
            approved = client.post(
                f"/api/v1/nexus-trade/proposals/{proposal_id}/approve",
                json={
                    "expected_revision": before["snapshot_version"],
                    "actor": "human:operator",
                    "request_id": "api-approve",
                    "reason": "reviewed in the authenticated dashboard",
                    "reinforced_confirmation": False,
                },
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(approved.status_code, 200)
        data = approved.json()["data"]
        self.assertEqual(data["transition"]["outcome"], "COMMITTED")
        self.assertEqual(
            data["snapshot"]["runtime"]["champion_version_id"],
            data["transition"]["after"]["champion_version_id"],
        )
        self.assertEqual(data["snapshot"]["snapshot_version"], data["transition"]["snapshot_version"])
        event_types = {event["type"] for event in data["snapshot"]["nexus_events"]}
        self.assertTrue({"nexus.proposal", "nexus.version_changed", "nexus.campaign"} <= event_types)
        serialized = json.dumps(data).lower()
        self.assertNotIn("dummy-dashboard", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("c:\\users", serialized)

    def test_approve_request_id_is_idempotent_and_cannot_be_rebound(self):
        proposal_id, _, _, before = self.seed_valid_proposal()
        first = asyncio.run(
            self.service.approve(
                proposal_id, before["snapshot_version"], "human:operator",
                request_id="approve-idempotent", reason="one reviewed decision",
            )
        )
        repeated = asyncio.run(
            PromotionService(self.db_path).approve(
                proposal_id, before["snapshot_version"], "human:operator",
                request_id="approve-idempotent", reason="one reviewed decision",
            )
        )
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["after"], first["after"])
        self.assertEqual(self.snapshot()["snapshot_version"], first["snapshot_version"])
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM nexus_transition_requests").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM nexus_versions WHERE status='CHAMPION'").fetchone()[0], 2
            )
        with self.assertRaises(PromotionConflict):
            asyncio.run(
                PromotionService(self.db_path).approve(
                    proposal_id, before["snapshot_version"], "human:operator",
                    request_id="approve-idempotent", reason="different decision",
                )
            )

    def test_reanalyze_request_id_replay_does_not_start_another_campaign(self):
        proposal_id, _, _, before = self.seed_valid_proposal()
        first = asyncio.run(
            self.service.reanalyze(
                proposal_id, before["snapshot_version"], "human:operator",
                request_id="reanalyze-idempotent", reason="same human decision",
            )
        )
        repeated = asyncio.run(
            PromotionService(self.db_path).reanalyze(
                proposal_id, before["snapshot_version"], "human:operator",
                request_id="reanalyze-idempotent", reason="same human decision",
            )
        )
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["after"], first["after"])
        self.assertEqual(self.snapshot()["snapshot_version"], first["snapshot_version"])
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM nexus_campaigns WHERE lane='challenger_trial'").fetchone()[0],
                2,
            )

    def test_legacy_audit_schema_migrates_without_losing_history(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.execute("DROP TABLE nexus_audit_events")
            db.execute(
                "CREATE TABLE nexus_audit_events ("
                "id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, "
                "before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', "
                "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "INSERT INTO nexus_audit_events(id,actor,action,before_json,after_json) "
                "VALUES ('legacy-audit','legacy-human','LEGACY','{}','{}')"
            )
            db.commit()

        asyncio.run(DatabaseRepository(self.db_path).init_db())
        asyncio.run(DatabaseRepository(self.db_path).init_db())

        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(nexus_audit_events)")}
            legacy = db.execute(
                "SELECT actor,action,outcome,reason,request_id FROM nexus_audit_events "
                "WHERE id='legacy-audit'"
            ).fetchone()
            governance_tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'nexus_%'"
                )
            }
        self.assertTrue(
            {"reason", "request_id", "expected_revision", "actual_revision", "outcome",
             "hashes_json", "error_code"} <= columns
        )
        self.assertEqual(legacy, ("legacy-human", "LEGACY", "COMMITTED", "", ""))
        self.assertTrue(
            {"nexus_transition_requests", "nexus_event_outbox", "nexus_trial_boundaries"}
            <= governance_tables
        )

    def test_approve_rejects_an_incomplete_gate_manifest_even_when_marked_evolve(self):
        proposal_id, _, _, before = self.seed_valid_proposal()
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            proposal = db.execute(
                "SELECT payload FROM nexus_proposals WHERE id=?", (proposal_id,)
            ).fetchone()[0]
            payload = json.loads(proposal)
            row = db.execute(
                "SELECT id,snapshot FROM nexus_reports WHERE id=?", (payload["report_id"],)
            ).fetchone()
            snapshot = json.loads(row[1])
            snapshot["gates"] = [
                gate for gate in snapshot["gates"] if gate["code"] != "PROFIT_FACTOR"
            ]
            encoded = canonical_json(snapshot)
            import hashlib
            report_hash = hashlib.sha256(
                b"nexus-report-json-v1\0" + encoded.encode("utf-8")
            ).hexdigest()
            payload["report_hash"] = report_hash
            db.execute("DROP TRIGGER trg_nexus_reports_no_update")
            db.execute(
                "UPDATE nexus_reports SET report_hash=?,snapshot=? WHERE id=?",
                (report_hash, encoded, row[0]),
            )
            db.execute(
                "UPDATE nexus_proposals SET payload=? WHERE id=?",
                (canonical_json(payload), proposal_id),
            )
            db.commit()

        with self.assertRaisesRegex(PromotionRejected, "GATE"):
            asyncio.run(
                self.service.approve(
                    proposal_id, before["snapshot_version"], "human:operator",
                    request_id="missing-gate", reason="must reject forged manifest",
                )
            )


if __name__ == "__main__":
    unittest.main()
