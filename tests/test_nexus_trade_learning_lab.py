import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from database.repository import DatabaseRepository
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.artifacts import canonical_json
from nexus_trade.constants import NEXUS_PROVENANCE_HASH, NEXUS_TRADE_BOT_ID
from nexus_trade.domain import Lane
from nexus_trade.indicators import IndicatorFrame
from nexus_trade.learning_lab import LearningLabService
from nexus_trade.promotion import PromotionService
from nexus_trade.reports import ReportService
from nexus_trade.scheduler import BRASILIA
from tests.test_nexus_trade_learning import ArtifactAndRegistryTests


class LearningEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "learning-lab.db")
        self.repository = DatabaseRepository(self.db_path)
        await self.repository.init_db()
        self.snapshot = await self.repository.get_nexus_runtime_snapshot()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    def seed_comparable_week(self, window):
        trial_campaign = next(
            item for item in self.snapshot["active_campaigns"]
            if item["lane"] == Lane.TRIAL.value
        )
        champion_campaign = next(
            item for item in self.snapshot["active_campaigns"]
            if item["lane"] == Lane.CHAMPION.value
        )
        versions = {
            item["lane"]: item["version"]["id"] for item in self.snapshot["lanes"]
        }
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            for campaign in (trial_campaign, champion_campaign):
                db.execute(
                    "UPDATE nexus_campaigns SET started_at=? WHERE id=?",
                    (window.start_utc.isoformat(), campaign["id"]),
                )
            contract_id = 7_000_000
            for day in range(7):
                count = 43 if day < 6 else 42
                epoch_base = int((window.start_utc + timedelta(days=day, hours=1)).timestamp())
                for lane, campaign, wins in (
                    (Lane.CHAMPION.value, champion_campaign, 26 if day < 6 else 24),
                    (Lane.TRIAL.value, trial_campaign, 31 if day < 6 else 24),
                ):
                    version_id = versions[lane]
                    for index in range(count):
                        signal_epoch = epoch_base + index * 60
                        decision_id = f"governed-{lane}-{day}-{index}"
                        contract_id += 1
                        won = index < wins
                        payload = json.dumps({
                            "lane": lane,
                            "campaign_id": campaign["id"],
                            "nexus_version_id": version_id,
                            "provenance_hash": NEXUS_PROVENANCE_HASH,
                        }, sort_keys=True, separators=(",", ":"))
                        features = json.dumps({
                            "schema_version": 1,
                            "provenance_hash": NEXUS_PROVENANCE_HASH,
                            "features": {"adx": 18.0, "bollinger_percent_b": 0.5},
                        }, sort_keys=True, separators=(",", ":"))
                        db.execute(
                            "INSERT OR IGNORE INTO nexus_candles "
                            "(symbol,open_epoch,close_epoch,open,high,low,close) "
                            "VALUES ('R_100',?,?,?,?,?,?)",
                            (signal_epoch, signal_epoch + 60, 100, 101, 99, 100.5),
                        )
                        db.execute(
                            "INSERT INTO nexus_features "
                            "(symbol,open_epoch,nexus_version_id,values_json) "
                            "VALUES ('R_100',?,?,?)",
                            (signal_epoch, version_id, features),
                        )
                        db.execute(
                            "INSERT INTO nexus_decisions "
                            "(id,lane,nexus_version_id,campaign_id,symbol,signal_epoch,entry_delay_ms,payload) "
                            "VALUES (?,?,?,?,?,?,100,?)",
                            (
                                decision_id, lane, version_id, campaign["id"],
                                "R_100", signal_epoch, payload,
                            ),
                        )
                        db.execute(
                            "INSERT INTO trades "
                            "(bot_id,strategy_name,symbol,contract_type,contract_id,stake,"
                            "payout,profit,result,status,purchase_time,expiry_time,lane,"
                            "nexus_version_id,campaign_id,decision_id,entry_delay_ms) "
                            "VALUES ('nexus-trade','nexus_trade','R_100','CALL',?,0.35,?,?,?,"
                            "'closed',?,?,?,?,?,?,100)",
                            (
                                contract_id, 0.66 if won else 0.0,
                                0.31 if won else -0.35, "won" if won else "lost",
                                signal_epoch + 60, signal_epoch + 118, lane,
                                version_id, campaign["id"], decision_id,
                            ),
                        )
            db.commit()
        finally:
            db.close()
        return LearningLabService(self.db_path)._active_trial_campaign()

    async def test_settled_trial_trade_rebuilds_one_exact_causal_learning_row(self):
        trial_version = self.snapshot["runtime"]["trial_version_id"]
        campaign_id = self.snapshot["active_campaigns"][0]["id"]
        feature_epoch = 60_000
        decision_id = "trial-learning-row"
        await self.repository.record_nexus_cycle_evidence(
            candle={
                "open_epoch": feature_epoch,
                "close_epoch": feature_epoch + 60,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
            },
            indicators=IndicatorFrame(
                epoch=feature_epoch,
                upper=103.0,
                middle=100.0,
                lower=97.0,
                adx=18.0,
                values={
                    "bollinger_percent_b": 0.67,
                    "bollinger_width": 6.0,
                    "rsi": 58.0,
                },
            ),
            version_ids={Lane.TRIAL.value: trial_version},
            provenance_hash=NEXUS_PROVENANCE_HASH,
        )
        await self.repository.record_nexus_decision(
            {
                "id": decision_id,
                "decision_id": decision_id,
                "lane": Lane.TRIAL.value,
                "signal_epoch": feature_epoch,
                "provenance_hash": NEXUS_PROVENANCE_HASH,
            },
            nexus_version_id=trial_version,
            campaign_id=campaign_id,
            state={"position_status": "IDLE"},
        )
        await self.repository.upsert_trade({
            "bot_id": NEXUS_TRADE_BOT_ID,
            "strategy_name": "nexus_trade",
            "symbol": "R_100",
            "contract_type": "CALL",
            "contract_id": 99101,
            "stake": 0.35,
            "payout": 0.66,
            "profit": 0.31,
            "result": "won",
            "status": "closed",
            "purchase_time": feature_epoch + 60,
            "expiry_time": feature_epoch + 118,
            "lane": Lane.TRIAL.value,
            "nexus_version_id": trial_version,
            "campaign_id": campaign_id,
            "decision_id": decision_id,
        })

        rows = await self.repository.list_nexus_learning_rows(
            campaign_id=campaign_id,
            cutoff_epoch=feature_epoch + 180,
            feature_names=("adx", "bollinger_percent_b", "rsi"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contract_id"], 99101)
        self.assertEqual(rows[0]["feature_epoch"], feature_epoch)
        self.assertEqual(rows[0]["entry_epoch"], feature_epoch + 60)
        self.assertEqual(rows[0]["label_epoch"], feature_epoch + 118)
        self.assertEqual(rows[0]["features"], {
            "adx": 18.0,
            "bollinger_percent_b": 0.67,
            "rsi": 58.0,
        })
        self.assertEqual(rows[0]["label"], 1)
        self.assertEqual(rows[0]["provenance_hash"], NEXUS_PROVENANCE_HASH)

    async def test_provisioned_v1_is_the_frozen_trial_and_first_model_is_shadow(self):
        registry = CandidateRegistry(self.db_path)
        baseline = registry.list_candidates()

        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]["id"], "candidate-nexus-trial-v1")
        self.assertEqual(baseline[0]["status"], "TRIAL")
        self.assertEqual(
            baseline[0]["nexus_version_id"],
            self.snapshot["runtime"]["trial_version_id"],
        )
        self.assertEqual(
            baseline[0]["metadata"]["artifact_type"],
            "nexus_trade_deterministic_baseline",
        )

        trained = registry.register(ArtifactAndRegistryTests.artifact("first-shadow"))

        self.assertEqual(trained["status"], "SHADOW")
        self.assertIsNone(trained["nexus_version_id"])

    async def test_due_daily_job_records_insufficient_data_once_across_restart(self):
        boundary = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
        first_service = LearningLabService(self.db_path)

        first = await first_service.run_due(now=boundary)
        restarted = LearningLabService(self.db_path)
        repeated = await restarted.run_due(now=boundary)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["job_type"], "daily_learning")
        self.assertEqual(first[0]["outcome"], "INSUFFICIENT_DATA")
        self.assertEqual(repeated, [])
        attempts = first_service.list_attempts()
        self.assertEqual(len(attempts), 5)
        self.assertEqual({item["status"] for item in attempts}, {"REJECTED"})
        self.assertEqual(
            {item["error_code"] for item in attempts},
            {"insufficient_complete_rows"},
        )

    async def test_due_daily_job_closes_one_real_report_and_exposes_its_identity(self):
        boundary = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
        service = LearningLabService(self.db_path)

        first = await service.run_due(now=boundary)
        repeated = await LearningLabService(self.db_path).run_due(now=boundary)

        self.assertEqual(repeated, [])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["job_type"], "daily_learning")
        self.assertRegex(first[0]["report_id"], r"^report-[0-9a-f]{24}$")
        reports = ReportService(self.db_path).list_reports()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["id"], first[0]["report_id"])
        self.assertEqual(reports[0]["snapshot"]["report_type"], "daily")
        self.assertEqual(
            reports[0]["snapshot"]["accumulated_progress"]["operations"],
            0,
        )

    async def test_failed_daily_job_is_retried_after_restart_instead_of_stalling_forever(self):
        boundary = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
        first_service = LearningLabService(self.db_path)

        async def fail_once(campaign, window):
            raise RuntimeError("transient training fault")

        first_service._process_daily = fail_once
        failed = await first_service.run_due(now=boundary)
        recovered = await LearningLabService(self.db_path).run_due(now=boundary)
        repeated = await LearningLabService(self.db_path).run_due(now=boundary)

        self.assertEqual(failed[0]["outcome"], "FAILED")
        self.assertEqual(recovered[0]["outcome"], "INSUFFICIENT_DATA")
        self.assertEqual(repeated, [])

    async def test_daily_job_trains_content_addressed_shadow_from_complete_rows(self):
        feature_names = (
            "adx", "bollinger_percent_b", "bollinger_z_score",
            "bollinger_width", "bollinger_slope", "adx_pdi", "adx_mdi",
            "body", "body_ratio", "upper_wick", "lower_wick",
            "upper_wick_ratio", "lower_wick_ratio",
        )
        trial_version = self.snapshot["runtime"]["trial_version_id"]
        campaign_id = self.snapshot["active_campaigns"][0]["id"]
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            for index in range(100):
                feature_epoch = 60 * (index + 1)
                won = index % 2 == 0
                decision_id = f"daily-training-{index:03d}"
                contract_id = 880_000 + index
                features = {
                    name: float((index % 7) + offset + (10 if won else 0))
                    for offset, name in enumerate(feature_names)
                }
                feature_payload = json.dumps({
                    "schema_version": 1,
                    "provenance_hash": NEXUS_PROVENANCE_HASH,
                    "features": features,
                }, sort_keys=True, separators=(",", ":"))
                decision_payload = json.dumps({
                    "decision": {
                        "id": decision_id,
                        "decision_id": decision_id,
                        "lane": Lane.TRIAL.value,
                        "signal_epoch": feature_epoch,
                        "provenance_hash": NEXUS_PROVENANCE_HASH,
                    },
                    "state": {"position_status": "IDLE"},
                    "owner": None,
                    "lane": Lane.TRIAL.value,
                    "nexus_version_id": trial_version,
                    "campaign_id": campaign_id,
                    "provenance_hash": NEXUS_PROVENANCE_HASH,
                }, sort_keys=True, separators=(",", ":"))
                db.execute(
                    "INSERT INTO nexus_candles "
                    "(symbol,open_epoch,close_epoch,open,high,low,close) "
                    "VALUES ('R_100',?,?,?,?,?,?)",
                    (feature_epoch, feature_epoch + 60, 100, 102, 99, 101),
                )
                db.execute(
                    "INSERT INTO nexus_features "
                    "(symbol,open_epoch,nexus_version_id,values_json) "
                    "VALUES ('R_100',?,?,?)",
                    (feature_epoch, trial_version, feature_payload),
                )
                db.execute(
                    "INSERT INTO nexus_decisions "
                    "(id,lane,nexus_version_id,campaign_id,symbol,signal_epoch,payload) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        decision_id, Lane.TRIAL.value, trial_version,
                        campaign_id, "R_100", feature_epoch, decision_payload,
                    ),
                )
                stake = 0.35
                payout = 0.66 if won else 0.0
                profit = 0.31 if won else -0.35
                db.execute(
                    "INSERT INTO trades "
                    "(bot_id,strategy_name,symbol,contract_type,contract_id,stake,"
                    "payout,profit,result,status,purchase_time,expiry_time,lane,"
                    "nexus_version_id,campaign_id,decision_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        NEXUS_TRADE_BOT_ID, "nexus_trade", "R_100", "CALL",
                        contract_id, stake, payout, profit,
                        "won" if won else "lost", "closed", feature_epoch + 60,
                        feature_epoch + 118, Lane.TRIAL.value, trial_version,
                        campaign_id, decision_id,
                    ),
                )
            db.commit()
        finally:
            db.close()
        boundary = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
        service = LearningLabService(self.db_path)

        result = await service.run_due(now=boundary)
        repeated = await LearningLabService(self.db_path).run_due(now=boundary)

        self.assertEqual(result[0]["outcome"], "CANDIDATES_TRAINED")
        self.assertEqual(len(result[0]["candidates"]), 1)
        self.assertEqual(result[0]["candidates"][0]["family"], "bollinger_adx_candle")
        self.assertEqual(repeated, [])
        candidates = CandidateRegistry(self.db_path).list_candidates()
        self.assertEqual([item["status"] for item in candidates], ["TRIAL", "SHADOW"])
        shadow = candidates[1]
        self.assertEqual(
            tuple(shadow["metadata"]["feature_schema"]),
            tuple(sorted(feature_names)),
        )
        self.assertEqual(shadow["metadata"]["direction_source"], "bollinger_v1_deterministic")
        qualification_attempts = [
            item for item in service.list_attempts()
            if item.get("candidate_id") == shadow["id"]
            and "qualification" in item
        ]
        self.assertEqual(len(qualification_attempts), 1)
        self.assertEqual(qualification_attempts[0]["status"], "SUCCEEDED")
        self.assertEqual(
            qualification_attempts[0]["qualification"]["status"], "PASS",
        )
        self.assertTrue(all(
            gate["status"] == "PASS"
            for gate in qualification_attempts[0]["qualification"]["gates"]
        ))

    async def test_monday_boundary_automatically_rotates_to_best_qualified_shadow_once(self):
        registry = CandidateRegistry(self.db_path)
        candidate = registry.register(ArtifactAndRegistryTests.artifact("weekly-trial"))
        LearningLabService(self.db_path).ledger.record({
            "schema_version": 1,
            "status": "SUCCEEDED",
            "candidate_id": candidate["id"],
            "artifact_hash": candidate["artifact_hash"],
            "dataset_hash": candidate["metadata"]["dataset_hash"],
            "provenance_hash": candidate["metadata"]["provenance_hash"],
            "seed": 73,
            "metrics": {},
            "ablations": [],
            "qualification": {
                "schema_version": 1,
                "status": "PASS",
                "gates": [{"name": "ARTIFACT_EXECUTABLE", "status": "PASS"}],
            },
        })
        started = LearningLabService._parse_timestamp(
            self.snapshot["active_campaigns"][0]["started_at"]
        ).astimezone(BRASILIA)
        days_until_monday = (7 - started.weekday()) % 7
        if days_until_monday == 0 and started.time() >= time(10, 0):
            days_until_monday = 7
        local_boundary = datetime.combine(
            started.date() + timedelta(days=days_until_monday),
            time(10, 0),
            BRASILIA,
        )
        boundary = local_boundary.astimezone(timezone.utc)
        before = await self.repository.get_nexus_runtime_snapshot()
        service = LearningLabService(self.db_path)

        results = await service.run_due(now=boundary)
        repeated = await LearningLabService(self.db_path).run_due(now=boundary)

        weekly = [item for item in results if item["job_type"] == "weekly_rotation"]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]["outcome"], "REPLACED")
        self.assertTrue(weekly[0]["changed"])
        self.assertEqual(repeated, [])
        after = await self.repository.get_nexus_runtime_snapshot()
        self.assertEqual(
            after["runtime"]["champion_version_id"],
            before["runtime"]["champion_version_id"],
        )
        self.assertNotEqual(
            after["runtime"]["trial_version_id"], before["runtime"]["trial_version_id"],
        )
        roles = {item["id"]: item["status"] for item in registry.list_candidates()}
        self.assertEqual(roles[candidate["id"]], "TRIAL")

    async def test_weekly_rotation_closes_governed_report_before_changing_trial(self):
        registry = CandidateRegistry(self.db_path)
        candidate = registry.register(ArtifactAndRegistryTests.artifact("weekly-report"))
        LearningLabService(self.db_path).ledger.record({
            "schema_version": 1,
            "status": "SUCCEEDED",
            "candidate_id": candidate["id"],
            "artifact_hash": candidate["artifact_hash"],
            "dataset_hash": candidate["metadata"]["dataset_hash"],
            "provenance_hash": candidate["metadata"]["provenance_hash"],
            "seed": 73,
            "metrics": {},
            "ablations": [],
            "qualification": {
                "schema_version": 1,
                "status": "PASS",
                "gates": [{"name": "ARTIFACT_EXECUTABLE", "status": "PASS"}],
            },
        })
        campaign = LearningLabService(self.db_path)._active_trial_campaign()
        started = LearningLabService._parse_timestamp(campaign["started_at"]).astimezone(BRASILIA)
        days_until_monday = (7 - started.weekday()) % 7
        if days_until_monday == 0 and started.time() >= time(10, 0):
            days_until_monday = 7
        boundary = datetime.combine(
            started.date() + timedelta(days=days_until_monday), time(10), BRASILIA,
        ).astimezone(timezone.utc)
        window = LearningLabService(self.db_path).schedule.weekly_window(boundary)

        result = await LearningLabService(self.db_path)._process_weekly(campaign, window)

        self.assertRegex(result["report_id"], r"^report-[0-9a-f]{24}$")
        report = ReportService(self.db_path).get_report(result["report_id"])
        self.assertIsNotNone(report)
        self.assertEqual(report.snapshot["report_type"], "weekly")
        self.assertEqual(report.snapshot["recommendation"], "INCONCLUSIVE")
        self.assertIsNone(result["proposal_id"])
        self.assertTrue(result["changed"])

    async def test_eligible_weekly_report_creates_one_hash_bound_human_proposal(self):
        registry = CandidateRegistry(self.db_path)
        artifact = ArtifactAndRegistryTests.artifact("proposal-current-trial")
        candidate = registry.register(artifact)
        LearningLabService(self.db_path).ledger.record({
            "schema_version": 1,
            "status": "SUCCEEDED",
            "candidate_id": candidate["id"],
            "artifact_hash": candidate["artifact_hash"],
            "dataset_hash": candidate["metadata"]["dataset_hash"],
            "provenance_hash": candidate["metadata"]["provenance_hash"],
            "seed": 73,
            "metrics": {},
            "ablations": [],
            "qualification": {
                "schema_version": 1,
                "status": "PASS",
                "gates": [{"name": "ARTIFACT_EXECUTABLE", "status": "PASS"}],
            },
        })
        started = LearningLabService._parse_timestamp(
            self.snapshot["active_campaigns"][0]["started_at"]
        ).astimezone(BRASILIA)
        days_until_monday = (7 - started.weekday()) % 7
        if days_until_monday == 0 and started.time() >= time(10):
            days_until_monday = 7
        boundary = datetime.combine(
            started.date() + timedelta(days=days_until_monday), time(10), BRASILIA,
        ).astimezone(timezone.utc)
        await PromotionService(self.db_path).replace_trial(
            boundary,
            actor="system:test",
            request_id="proposal-trial-rotation",
            reason="prepare executable Trial",
            candidate_id=candidate["id"],
        )
        control = await self.repository.get_nexus_control_snapshot()
        trial_lane = next(
            item for item in control["lanes"] if item["lane"] == Lane.TRIAL.value
        )
        trial_campaign = next(
            item for item in control["active_campaigns"]
            if item["lane"] == Lane.TRIAL.value
        )
        version = trial_lane["version"]
        report_snapshot = {
            "schema_version": 1,
            "report_type": "weekly",
            "campaign_id": trial_campaign["id"],
            "window": {
                "start_utc": boundary.isoformat(),
                "end_utc": (boundary + timedelta(days=7)).isoformat(),
            },
            "accumulated_progress": {
                "operations": 300, "target": 300,
                "complete_days": 7, "required_days": 7,
            },
            "recommendation": "EVOLVE",
            "gates": [{"code": "MINIMUM_SAMPLE", "status": "PASS"}],
            "trial": {
                "version_id": version["id"],
                "version_hash": version["version_hash"],
                "candidate_id": candidate["id"],
                "artifact_hash": artifact.artifact_hash,
                "metadata_hash": artifact.metadata_hash,
                "configuration_hash": artifact.metadata["configuration_hash"],
                "dataset_hash": artifact.metadata["dataset_hash"],
                "provenance_hash": artifact.metadata["provenance_hash"],
            },
        }
        encoded = canonical_json(report_snapshot)
        report_hash = hashlib.sha256(
            b"nexus-report-json-v1\0" + encoded.encode("utf-8")
        ).hexdigest()
        report_id = f"report-{report_hash[:24]}"
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                "INSERT INTO nexus_reports "
                "(id,campaign_id,report_hash,snapshot,report_type,window_start_utc,window_end_utc) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    report_id, trial_campaign["id"], report_hash, encoded, "weekly",
                    boundary.isoformat(), (boundary + timedelta(days=7)).isoformat(),
                ),
            )
            db.commit()
        finally:
            db.close()
        report = ReportService(self.db_path).get_report(report_id)
        service = LearningLabService(self.db_path)

        first = service._ensure_proposal(report)
        repeated = service._ensure_proposal(report)

        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(first["status"], "PENDING_USER_REVIEW")
        self.assertTrue(first["created"])
        self.assertFalse(repeated["created"])
        proposals = await self.repository.list_nexus_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["id"], first["id"])
        self.assertEqual(proposals[0]["payload"]["report_hash"], report_hash)
        self.assertEqual(
            proposals[0]["payload"]["artifact_hash"], artifact.artifact_hash,
        )

    async def test_weekly_governance_evidence_is_real_idempotent_and_report_consumable(self):
        boundary = datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc)
        window = LearningLabService(self.db_path).schedule.weekly_window(boundary)
        campaign = self.seed_comparable_week(window)
        service = LearningLabService(self.db_path)

        first = service._record_weekly_promotion_evidence(campaign, window)
        second = service._record_weekly_promotion_evidence(campaign, window)
        report = ReportService(self.db_path).close_weekly(window)

        self.assertEqual(first["attempt_hash"], second["attempt_hash"])
        attempts = [
            item for item in service.list_attempts()
            if item.get("attempt_type") == "promotion_evidence"
        ]
        self.assertEqual(len(attempts), 1)
        evidence = attempts[0]["promotion_evidence"]
        self.assertTrue(all(evidence["integrity"].values()))
        self.assertGreaterEqual(evidence["dsr_probability"], 0.95)
        self.assertLessEqual(evidence["pbo"], 0.10)
        self.assertTrue(evidence["sensitivity_passed"])
        self.assertEqual(report.snapshot["accumulated_progress"]["operations"], 300)
        self.assertNotEqual(report.snapshot["recommendation"], "INCONCLUSIVE")

    async def test_forever_runner_processes_without_user_action_and_stops_cleanly(self):
        service = LearningLabService(self.db_path)
        stop = __import__("asyncio").Event()
        calls = []

        async def run_due(*, now=None):
            calls.append(now)
            stop.set()
            return []

        service.run_due = run_due

        await service.run_forever(stop, poll_seconds=0.01)

        self.assertEqual(len(calls), 1)

    async def test_forever_runner_publishes_committed_learning_state_to_frontend(self):
        service = LearningLabService(self.db_path)
        stop = __import__("asyncio").Event()

        class Publisher:
            def __init__(self):
                self.events = []

            async def publish(self, event):
                self.events.append(event)
                return True

        publisher = Publisher()

        async def run_due(*, now=None):
            stop.set()
            return [{
                "job_type": "daily_learning",
                "campaign_id": self.snapshot["active_campaigns"][0]["id"],
                "window_end_utc": "2026-08-12T13:00:00+00:00",
                "outcome": "INSUFFICIENT_DATA",
                "events": [],
            }]

        service.run_due = run_due
        await service.run_forever(stop, poll_seconds=0.01, publisher=publisher)

        self.assertEqual([event["type"] for event in publisher.events], ["nexus.learning"])
        event = publisher.events[0]
        self.assertGreaterEqual(event["snapshot_version"], 1)
        self.assertIn("learning", event["payload"])
        self.assertIn("jobs", event["payload"]["learning"])


if __name__ == "__main__":
    unittest.main()
