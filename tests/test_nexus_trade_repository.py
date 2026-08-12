import asyncio
import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

import aiosqlite

from database.repository import DatabaseRepository
from database.models import DatabaseModels
from nexus_trade.constants import NEXUS_PROVENANCE_HASH, NEXUS_TRADE_BOT_ID
from nexus_trade.artifacts import canonical_json
from nexus_trade.domain import CampaignStatus, Lane, VersionStatus
from nexus_trade.repository import NexusTradeRepository, NexusTradeSingletonError
from tests.test_nexus_trade_learning import ArtifactAndRegistryTests


class NexusTradeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "nexus-trade.db")
        self.repo = DatabaseRepository(self.db_path)
        self.nexus = NexusTradeRepository(self.db_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _champion_management_payload(**overrides):
        payload = {
            "initial_stake": 0.7,
            "money_management": "martingale",
            "money_config": {"multiplier": 2.1, "max_levels": 3},
            "risk_config": {
                "take_profit_daily": 12.0,
                "stop_loss_daily": 7.0,
                "max_daily_trades": 30,
                "max_single_stake": 8.0,
                "max_consecutive_losses": 4,
                "cooldown_minutes": 10,
            },
        }
        payload.update(overrides)
        return payload

    async def _seed_shared_v1_legacy(self, db_path: str, campaign_id: str) -> str:
        repository = DatabaseRepository(db_path)
        nexus = NexusTradeRepository(db_path)
        await repository.init_db()
        snapshot = await nexus.get_runtime_snapshot()
        champion_id = snapshot["runtime"]["champion_version_id"]
        trial_id = snapshot["runtime"]["trial_version_id"]
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "DELETE FROM nexus_campaigns WHERE lane = ? AND status = 'ACTIVE'",
                (Lane.TRIAL.value,),
            )
            await db.execute(
                "UPDATE nexus_runtime SET trial_version_id = ? WHERE bot_id = ?",
                (champion_id, NEXUS_TRADE_BOT_ID),
            )
            await db.execute("DELETE FROM nexus_versions WHERE id = ?", (trial_id,))
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,'ACTIVE')",
                (campaign_id, Lane.TRIAL.value, champion_id),
            )
            await db.commit()
        return champion_id

    async def test_init_migrates_exact_legacy_decision_provenance_for_reports(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        version_id = snapshot["runtime"]["trial_version_id"]
        campaign_id = snapshot["active_campaigns"][0]["id"]
        decision_id = "legacy-provenance-decision"
        legacy = json.dumps({
            "decision": {
                "id": decision_id,
                "decision_id": decision_id,
                "lane": Lane.TRIAL.value,
                "signal_epoch": 60_000,
            },
            "state": {"position_status": "IDLE"},
            "owner": None,
        }, sort_keys=True, separators=(",", ":"))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO nexus_decisions "
                "(id,lane,nexus_version_id,campaign_id,symbol,signal_epoch,payload) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    decision_id, Lane.TRIAL.value, version_id, campaign_id,
                    "R_100", 60_000, legacy,
                ),
            )
            await db.execute(
                "INSERT INTO trades "
                "(bot_id,strategy_name,symbol,contract_type,contract_id,stake,"
                "payout,profit,result,status,purchase_time,expiry_time,lane,"
                "nexus_version_id,campaign_id,decision_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    NEXUS_TRADE_BOT_ID, "nexus_trade", "R_100", "CALL", 99111,
                    0.35, 0.66, 0.31, "won", "closed", 60_060, 60_118,
                    Lane.TRIAL.value, version_id, campaign_id, decision_id,
                ),
            )
            await db.execute(
                "DELETE FROM nexus_repository_meta "
                "WHERE key='nexus_decision_provenance_v1'"
            )
            await db.commit()

        await DatabaseRepository(self.db_path).init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT payload FROM nexus_decisions WHERE id=?", (decision_id,),
            ) as cursor:
                payload = json.loads((await cursor.fetchone())[0])
        self.assertEqual(payload["lane"], Lane.TRIAL.value)
        self.assertEqual(payload["nexus_version_id"], version_id)
        self.assertEqual(payload["campaign_id"], campaign_id)
        self.assertEqual(payload["provenance_hash"], NEXUS_PROVENANCE_HASH)
        self.assertEqual(
            payload["decision"]["provenance_hash"], NEXUS_PROVENANCE_HASH,
        )

    async def test_init_provisions_exactly_one_nexus_trade(self):
        await self.repo.init_db()
        await self.repo.init_db()

        bots = [bot for bot in await self.repo.list_bots() if bot["strategy_id"] == "nexus_trade"]

        self.assertEqual([bot["id"] for bot in bots], [NEXUS_TRADE_BOT_ID])
        self.assertEqual(bots[0]["symbol"], "R_100")
        self.assertEqual(bots[0]["timeframe_seconds"], 60)
        self.assertEqual(bots[0]["duration"], 58)
        self.assertEqual(bots[0]["duration_unit"], "s")

    async def test_snapshot_has_versioned_champion_and_active_trial_campaign(self):
        await self.repo.init_db()

        snapshot = await self.nexus.get_runtime_snapshot()

        self.assertEqual(snapshot["bot"]["id"], "nexus-trade")
        self.assertEqual(
            {lane["lane"] for lane in snapshot["lanes"]},
            {Lane.CHAMPION.value, Lane.TRIAL.value},
        )
        champion = next(lane for lane in snapshot["lanes"] if lane["lane"] == Lane.CHAMPION.value)
        trial = next(lane for lane in snapshot["lanes"] if lane["lane"] == Lane.TRIAL.value)
        self.assertEqual(champion["version"]["status"], VersionStatus.CHAMPION.value)
        self.assertEqual(trial["version"]["status"], VersionStatus.TRIAL.value)
        self.assertNotEqual(champion["version"]["id"], trial["version"]["id"])
        self.assertEqual(champion["version"]["name"], "Champion V1")
        self.assertEqual(champion["version"]["snapshot"]["bollinger"], {"period": 20, "std_dev": 2, "ma": "SMA"})
        self.assertEqual(champion["version"]["snapshot"]["adx"], {"period": 14, "max_entry": 22})
        self.assertEqual(len(snapshot["active_campaigns"]), 2)
        self.assertEqual(
            {item["lane"] for item in snapshot["active_campaigns"]},
            {Lane.CHAMPION.value, Lane.TRIAL.value},
        )
        self.assertTrue(all(
            item["status"] == CampaignStatus.ACTIVE.value
            for item in snapshot["active_campaigns"]
        ))
        self.assertEqual(
            snapshot["active_campaigns"][0]["nexus_version_id"],
            snapshot["runtime"]["trial_version_id"],
        )

    async def test_snapshot_counts_only_closed_trades_from_the_active_trial_campaign(self):
        await self.repo.init_db()
        initial = await self.nexus.get_runtime_snapshot()
        campaign = initial["active_campaigns"][0]
        trial_version = initial["runtime"]["trial_version_id"]
        champion_version = initial["runtime"]["champion_version_id"]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO nexus_campaigns "
                "(id,lane,nexus_version_id,status,ended_at) "
                "VALUES ('older-campaign',?,?, 'CLOSED',CURRENT_TIMESTAMP)",
                (Lane.TRIAL.value, trial_version),
            )
            await db.commit()

        trades = (
            (8101, Lane.TRIAL.value, trial_version, campaign["id"], "closed"),
            (8102, Lane.TRIAL.value, trial_version, campaign["id"], "closed"),
            (8103, Lane.TRIAL.value, trial_version, campaign["id"], "open"),
            (8104, Lane.TRIAL.value, trial_version, "older-campaign", "closed"),
            (8105, Lane.CHAMPION.value, champion_version, campaign["id"], "closed"),
        )
        for contract_id, lane, version_id, campaign_id, status in trades:
            await self.repo.upsert_trade({
                "bot_id": NEXUS_TRADE_BOT_ID,
                "strategy_name": "nexus_trade",
                "symbol": "R_100",
                "contract_type": "CALL",
                "contract_id": contract_id,
                "stake": 0.35,
                "payout": 0.66 if status == "closed" else None,
                "profit": 0.31 if status == "closed" else None,
                "result": "won" if status == "closed" else None,
                "status": status,
                "lane": lane,
                "nexus_version_id": version_id,
                "campaign_id": campaign_id,
            })

        snapshot = await self.nexus.get_runtime_snapshot()

        self.assertEqual(
            snapshot["active_campaigns"][0].get("progress"),
            {"completed": 2, "target": 300},
        )

    async def test_exact_legacy_v1_pointer_and_campaign_are_migrated_to_a_trial_role(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        champion_id = snapshot["runtime"]["champion_version_id"]
        trial_id = snapshot["runtime"]["trial_version_id"]
        if champion_id != trial_id:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM nexus_campaigns WHERE lane = ? AND status = 'ACTIVE'",
                    (Lane.TRIAL.value,),
                )
                await db.execute(
                    "UPDATE nexus_runtime SET trial_version_id = ? WHERE bot_id = ?",
                    (champion_id, NEXUS_TRADE_BOT_ID),
                )
                await db.execute("DELETE FROM nexus_versions WHERE id = ?", (trial_id,))
                await db.execute(
                    "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                    "VALUES (?,?,?,'ACTIVE')",
                    (f"trial-{champion_id}", Lane.TRIAL.value, champion_id),
                )
                await db.commit()

        await self.repo.init_db()
        migrated = await self.nexus.get_runtime_snapshot()
        trial = next(
            item["version"] for item in migrated["lanes"]
            if item["lane"] == Lane.TRIAL.value
        )
        self.assertEqual(trial["status"], VersionStatus.TRIAL.value)
        self.assertNotEqual(
            migrated["runtime"]["champion_version_id"],
            migrated["runtime"]["trial_version_id"],
        )
        self.assertEqual(
            migrated["active_campaigns"][0]["nexus_version_id"], trial["id"],
        )

    async def test_post_governance_legacy_campaign_ids_migrate_and_survive_restart(self):
        for campaign_id in (
            "trial-reanalyze-review-123456789abc",
            "trial-after-approve-123456789abc",
        ):
            with self.subTest(campaign_id=campaign_id), tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "post-governance-legacy.db")
                champion_id = await self._seed_shared_v1_legacy(db_path, campaign_id)

                try:
                    await DatabaseRepository(db_path).init_db()
                except NexusTradeSingletonError as exc:
                    self.fail(f"valid post-governance legacy state was rejected: {exc}")
                migrated = await NexusTradeRepository(db_path).get_runtime_snapshot()

                self.assertEqual(migrated["runtime"]["champion_version_id"], champion_id)
                self.assertNotEqual(migrated["runtime"]["trial_version_id"], champion_id)
                self.assertEqual(migrated["active_campaigns"][0]["id"], campaign_id)
                self.assertEqual(
                    migrated["active_campaigns"][0]["nexus_version_id"],
                    migrated["runtime"]["trial_version_id"],
                )

                restarted_repository = DatabaseRepository(db_path)
                restarted_nexus = NexusTradeRepository(db_path)
                await restarted_repository.init_db()
                restarted = await restarted_nexus.get_runtime_snapshot()
                self.assertEqual(restarted["runtime"], migrated["runtime"])
                self.assertEqual(restarted["active_campaigns"], migrated["active_campaigns"])

    async def test_post_governance_legacy_migration_rolls_back_atomically(self):
        campaign_id = "trial-after-rollback-123456789abc"
        champion_id = await self._seed_shared_v1_legacy(self.db_path, campaign_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TRIGGER abort_post_governance_legacy_migration
                BEFORE UPDATE OF trial_version_id ON nexus_runtime
                WHEN OLD.trial_version_id != NEW.trial_version_id
                BEGIN SELECT RAISE(ABORT, 'forced legacy migration failure'); END;
                """
            )
            await db.commit()

        try:
            await DatabaseRepository(self.db_path).init_db()
        except aiosqlite.IntegrityError:
            pass
        except NexusTradeSingletonError as exc:
            self.fail(f"migration did not reach the transactional update: {exc}")
        else:
            self.fail("injected legacy migration failure was not raised")

        async with aiosqlite.connect(self.db_path) as db:
            runtime = await (await db.execute(
                "SELECT trial_version_id FROM nexus_runtime WHERE bot_id = ?",
                (NEXUS_TRADE_BOT_ID,),
            )).fetchone()
            campaign = await (await db.execute(
                "SELECT id,nexus_version_id FROM nexus_campaigns "
                "WHERE lane = ? AND status = 'ACTIVE'",
                (Lane.TRIAL.value,),
            )).fetchone()
            trial_version_count = await (await db.execute(
                "SELECT COUNT(*) FROM nexus_versions WHERE status = 'TRIAL'",
            )).fetchone()
            self.assertEqual(runtime[0], champion_id)
            self.assertEqual(tuple(campaign), (campaign_id, champion_id))
            self.assertEqual(trial_version_count[0], 0)
            await db.execute("DROP TRIGGER abort_post_governance_legacy_migration")
            await db.commit()

        await DatabaseRepository(self.db_path).init_db()
        restarted = await NexusTradeRepository(self.db_path).get_runtime_snapshot()
        self.assertNotEqual(restarted["runtime"]["trial_version_id"], champion_id)
        self.assertEqual(restarted["active_campaigns"][0]["id"], campaign_id)

    async def test_wrong_role_state_with_another_valid_trial_is_not_legacy_migrated(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        champion_id = snapshot["runtime"]["champion_version_id"]
        trial_v1_id = snapshot["runtime"]["trial_version_id"]
        artifact = ArtifactAndRegistryTests.artifact("alternative-valid-trial")
        candidate_id = f"candidate-{artifact.artifact_hash[:24]}"
        version_snapshot = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "artifact": json.loads(artifact.to_json()),
            "trial_selection": {"request_id": "alternative-valid-trial"},
        }
        encoded = canonical_json(version_snapshot)
        version_hash = hashlib.sha256(
            b"nexus-trial-version-v1\0" + encoded.encode("utf-8")
        ).hexdigest()
        alternative_trial_id = f"nexus-trial-{version_hash[:16]}"

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("DROP TRIGGER trg_nexus_candidates_immutable_status")
            await db.execute(
                "UPDATE nexus_candidates SET status='SHADOW' "
                "WHERE id='candidate-nexus-trial-v1'"
            )
            await db.execute(
                "DELETE FROM nexus_campaigns WHERE lane = ? AND status = 'ACTIVE'",
                (Lane.TRIAL.value,),
            )
            await db.execute(
                "UPDATE nexus_runtime SET trial_version_id = ? WHERE bot_id = ?",
                (champion_id, NEXUS_TRADE_BOT_ID),
            )
            await db.execute("DELETE FROM nexus_versions WHERE id = ?", (trial_v1_id,))
            await db.execute(
                "INSERT INTO nexus_candidates "
                "(id,nexus_version_id,artifact_hash,status,metadata) "
                "VALUES (?,?,?,'TRIAL',?)",
                (
                    candidate_id,
                    alternative_trial_id,
                    artifact.artifact_hash,
                    artifact.to_json(),
                ),
            )
            await db.execute(
                "INSERT INTO nexus_versions(id,name,status,version_hash,snapshot) "
                "VALUES (?,?,'TRIAL',?,?)",
                (
                    alternative_trial_id,
                    "Alternative Valid Trial",
                    version_hash,
                    encoded,
                ),
            )
            await db.execute(
                "INSERT INTO nexus_campaigns(id,lane,nexus_version_id,status) "
                "VALUES ('fresh-wrong-role-state',?,?, 'ACTIVE')",
                (Lane.TRIAL.value, champion_id),
            )
            await db.commit()

        with self.assertRaises(NexusTradeSingletonError):
            await self.repo.init_db()

        trial_v1_id, _, _ = NexusTradeRepository._v1_identity(VersionStatus.TRIAL)
        async with aiosqlite.connect(self.db_path) as db:
            runtime = await (await db.execute(
                "SELECT trial_version_id FROM nexus_runtime WHERE bot_id = ?",
                (NEXUS_TRADE_BOT_ID,),
            )).fetchone()
            campaign = await (await db.execute(
                "SELECT id,nexus_version_id FROM nexus_campaigns "
                "WHERE lane = ? AND status = 'ACTIVE'",
                (Lane.TRIAL.value,),
            )).fetchone()
            created_trial_v1 = await (await db.execute(
                "SELECT COUNT(*) FROM nexus_versions WHERE id = ?", (trial_v1_id,),
            )).fetchone()
        self.assertEqual(runtime[0], champion_id)
        self.assertEqual(tuple(campaign), ("fresh-wrong-role-state", champion_id))
        self.assertEqual(created_trial_v1[0], 0)

    async def test_snapshot_and_reinitialization_reject_wrong_role_pointers(self):
        for pointer, source_lane in (
            ("champion_version_id", Lane.TRIAL),
            ("trial_version_id", Lane.CHAMPION),
        ):
            with self.subTest(pointer=pointer), tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "wrong-role.db")
                repository = DatabaseRepository(db_path)
                nexus = NexusTradeRepository(db_path)
                await repository.init_db()
                snapshot = await nexus.get_runtime_snapshot()
                source_id = next(
                    item["version"]["id"] for item in snapshot["lanes"]
                    if item["lane"] == source_lane.value
                )
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        f"UPDATE nexus_runtime SET {pointer} = ? WHERE bot_id = ?",
                        (source_id, NEXUS_TRADE_BOT_ID),
                    )
                    if pointer == "trial_version_id":
                        await db.execute(
                            "UPDATE nexus_campaigns SET nexus_version_id = ? "
                            "WHERE lane = ? AND status = 'ACTIVE'",
                            (source_id, Lane.TRIAL.value),
                        )
                    await db.commit()
                with self.assertRaises(NexusTradeSingletonError):
                    await nexus.get_runtime_snapshot()
                with self.assertRaises(NexusTradeSingletonError):
                    await repository.init_db()

    async def test_snapshot_and_reinitialization_reject_missing_or_mismatched_trial_campaign(self):
        for corruption in ("missing", "mismatched"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "campaign-corrupt.db")
                repository = DatabaseRepository(db_path)
                nexus = NexusTradeRepository(db_path)
                await repository.init_db()
                snapshot = await nexus.get_runtime_snapshot()
                champion_id = snapshot["runtime"]["champion_version_id"]
                async with aiosqlite.connect(db_path) as db:
                    if corruption == "missing":
                        await db.execute(
                            "DELETE FROM nexus_campaigns WHERE lane = ? AND status = 'ACTIVE'",
                            (Lane.TRIAL.value,),
                        )
                    else:
                        await db.execute(
                            "UPDATE nexus_campaigns SET nexus_version_id = ? "
                            "WHERE lane = ? AND status = 'ACTIVE'",
                            (champion_id, Lane.TRIAL.value),
                        )
                    await db.commit()
                with self.assertRaises(NexusTradeSingletonError):
                    await nexus.get_runtime_snapshot()
                with self.assertRaises(NexusTradeSingletonError):
                    await repository.init_db()

    async def test_duplicate_active_trial_campaign_fails_closed_on_snapshot_and_reinit(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        trial_id = snapshot["runtime"]["trial_version_id"]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DROP INDEX ux_nexus_campaigns_active_trial")
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES ('duplicate-trial','challenger_trial',?,'ACTIVE')",
                (trial_id,),
            )
            await db.commit()

        with self.assertRaises(NexusTradeSingletonError):
            await self.nexus.get_runtime_snapshot()
        with self.assertRaises((NexusTradeSingletonError, aiosqlite.IntegrityError)):
            await self.repo.init_db()

    async def test_malformed_pointer_and_campaign_identity_fail_closed(self):
        for corruption in ("pointer", "campaign"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "malformed.db")
                repository = DatabaseRepository(db_path)
                nexus = NexusTradeRepository(db_path)
                await repository.init_db()
                async with aiosqlite.connect(db_path) as db:
                    if corruption == "pointer":
                        await db.execute(
                            "UPDATE nexus_runtime SET trial_version_id = '' WHERE bot_id = ?",
                            (NEXUS_TRADE_BOT_ID,),
                        )
                    else:
                        await db.execute(
                            "UPDATE nexus_campaigns SET id = '' "
                            "WHERE lane = ? AND status = 'ACTIVE'",
                            (Lane.TRIAL.value,),
                        )
                    await db.commit()
                with self.assertRaises(NexusTradeSingletonError):
                    await nexus.get_runtime_snapshot()
                with self.assertRaises(NexusTradeSingletonError):
                    await repository.init_db()

    async def test_wal_snapshot_stays_on_one_revision_during_atomic_trial_rotation(self):
        await self.repo.init_db()
        artifact = ArtifactAndRegistryTests.artifact("concurrent-trial-snapshot")
        candidate_id = f"candidate-{artifact.artifact_hash[:24]}"
        version_snapshot = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "artifact": json.loads(artifact.to_json()),
            "trial_selection": {"request_id": "concurrent-rotation"},
        }
        encoded = canonical_json(version_snapshot)
        version_hash = hashlib.sha256(
            b"nexus-trial-version-v1\0" + encoded.encode("utf-8")
        ).hexdigest()
        version_id = f"nexus-trial-{version_hash[:16]}"
        campaign_id = f"trial-{version_hash[:16]}-concurrent"

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DROP TRIGGER trg_nexus_candidates_immutable_status")
            await db.execute(
                "UPDATE nexus_candidates SET status='SHADOW' "
                "WHERE id='candidate-nexus-trial-v1'"
            )
            await db.commit()

        class PausingSnapshotRepository(NexusTradeRepository):
            after_champion = asyncio.Event()
            resume_reader = asyncio.Event()
            paused = False

            @classmethod
            async def _validated_version(cls, db, version, *, lane):
                result = await super()._validated_version(db, version, lane=lane)
                if lane is Lane.CHAMPION and not cls.paused:
                    cls.paused = True
                    cls.after_champion.set()
                    await cls.resume_reader.wait()
                return result

        reader = PausingSnapshotRepository(self.db_path)
        reader_task = asyncio.create_task(reader.get_runtime_snapshot())
        await asyncio.wait_for(PausingSnapshotRepository.after_champion.wait(), timeout=1)

        async with aiosqlite.connect(self.db_path, timeout=1.0) as writer:
            await writer.execute("PRAGMA foreign_keys=ON")
            await writer.execute("BEGIN IMMEDIATE")
            await writer.execute(
                "INSERT INTO nexus_candidates "
                "(id,nexus_version_id,artifact_hash,status,metadata) "
                "VALUES (?,?,?,'TRIAL',?)",
                (candidate_id, version_id, artifact.artifact_hash, artifact.to_json()),
            )
            await writer.execute(
                "INSERT INTO nexus_versions(id,name,status,version_hash,snapshot) "
                "VALUES (?,?,'TRIAL',?,?)",
                (version_id, "Concurrent Trial", version_hash, encoded),
            )
            await writer.execute(
                "UPDATE nexus_campaigns SET status='SUPERSEDED',ended_at=CURRENT_TIMESTAMP "
                "WHERE lane=? AND status='ACTIVE'",
                (Lane.TRIAL.value,),
            )
            await writer.execute(
                "INSERT INTO nexus_campaigns(id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,'ACTIVE')",
                (campaign_id, Lane.TRIAL.value, version_id),
            )
            await writer.execute(
                "UPDATE nexus_runtime SET trial_version_id=? WHERE bot_id=?",
                (version_id, NEXUS_TRADE_BOT_ID),
            )
            await writer.execute(
                "UPDATE bot_instances SET config_revision=config_revision+1 WHERE id=?",
                (NEXUS_TRADE_BOT_ID,),
            )
            await asyncio.wait_for(writer.commit(), timeout=1)

        PausingSnapshotRepository.resume_reader.set()
        try:
            snapshot = await asyncio.wait_for(reader_task, timeout=1)
        except NexusTradeSingletonError as exc:
            self.fail(f"WAL reader mixed atomic rotation revisions: {exc}")

        trial = next(
            item["version"] for item in snapshot["lanes"]
            if item["lane"] == Lane.TRIAL.value
        )
        self.assertEqual(trial["id"], snapshot["runtime"]["trial_version_id"])
        self.assertEqual(
            snapshot["active_campaigns"][0]["nexus_version_id"], trial["id"],
        )
        latest = await NexusTradeRepository(self.db_path).get_runtime_snapshot()
        self.assertEqual(latest["runtime"]["trial_version_id"], version_id)
        self.assertEqual(latest["active_campaigns"][0]["id"], campaign_id)

    async def test_cancelled_wal_snapshot_releases_its_read_transaction(self):
        await self.repo.init_db()

        class CancellingSnapshotRepository(NexusTradeRepository):
            reader_started = asyncio.Event()
            never_resume = asyncio.Event()

            @classmethod
            async def _validated_version(cls, db, version, *, lane):
                result = await super()._validated_version(db, version, lane=lane)
                if lane is Lane.CHAMPION:
                    cls.reader_started.set()
                    await cls.never_resume.wait()
                return result

        task = asyncio.create_task(
            CancellingSnapshotRepository(self.db_path).get_runtime_snapshot(),
        )
        await asyncio.wait_for(
            CancellingSnapshotRepository.reader_started.wait(), timeout=1,
        )
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

        async with aiosqlite.connect(self.db_path, timeout=1.0) as writer:
            await asyncio.wait_for(writer.execute("BEGIN IMMEDIATE"), timeout=1)
            await writer.execute(
                "UPDATE nexus_runtime SET updated_at=updated_at WHERE bot_id=?",
                (NEXUS_TRADE_BOT_ID,),
            )
            await asyncio.wait_for(writer.rollback(), timeout=1)

    async def test_fresh_repository_has_separate_active_campaign_provenance_for_both_lanes(self):
        await self.repo.init_db()
        campaigns = await self.nexus.list_campaigns()

        active = {row["lane"]: row for row in campaigns if row["status"] == "ACTIVE"}
        self.assertEqual(set(active), {Lane.CHAMPION.value, Lane.TRIAL.value})
        self.assertNotEqual(active[Lane.CHAMPION.value]["id"], active[Lane.TRIAL.value]["id"])

    async def test_schema_rejects_a_second_nexus_trade_singleton(self):
        await self.repo.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO bot_instances (
                        id, name, strategy_id, strategy_config, account_id, account_type,
                        symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                        money_management, money_config, risk_config
                    ) VALUES (?, ?, 'nexus_trade', '{}', '', 'demo', 'R_100', 60, 58, 's', 0.35, 'fixed', '{}', '{}')
                    """,
                    ("another-nexus-trade", "Duplicate NexusTrade"),
                )

    async def test_nexus_migrations_add_lane_identity_to_trade_journals(self):
        await self.repo.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            trade_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(trades)")).fetchall()}
            intent_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(order_intents)")).fetchall()}

        expected = {"lane", "nexus_version_id", "campaign_id", "decision_id", "entry_delay_ms"}
        self.assertTrue(expected.issubset(trade_columns))
        self.assertTrue(expected.issubset(intent_columns))

    async def test_provisioning_rolls_back_the_bot_when_version_creation_fails(self):
        await self.repo.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("DELETE FROM nexus_campaigns")
            await db.execute("DELETE FROM nexus_runtime")
            await db.execute("DELETE FROM nexus_versions")
            await db.execute("DELETE FROM nexus_champion_management")
            await db.execute("DELETE FROM bot_instances WHERE id = ?", (NEXUS_TRADE_BOT_ID,))
            await db.execute(
                """
                CREATE TRIGGER fail_nexus_version_creation
                BEFORE INSERT ON nexus_versions
                BEGIN SELECT RAISE(ABORT, 'forced version failure'); END;
                """
            )
            await db.commit()

        with self.assertRaises(aiosqlite.IntegrityError):
            await self.nexus.ensure_singleton()

        async with aiosqlite.connect(self.db_path) as db:
            bots = await (await db.execute(
                "SELECT id FROM bot_instances WHERE strategy_id = 'nexus_trade'"
            )).fetchall()
        self.assertEqual(bots, [])

    async def test_concurrent_provisioning_keeps_one_consistent_singleton(self):
        await self.repo.init_db()

        snapshots = await asyncio.gather(
            self.nexus.ensure_singleton(), self.nexus.ensure_singleton(), self.nexus.ensure_singleton(),
        )

        self.assertTrue(all(snapshot["bot"]["id"] == "nexus-trade" for snapshot in snapshots))
        snapshot = await self.nexus.get_runtime_snapshot()
        self.assertEqual(len(snapshot["lanes"]), 2)
        self.assertEqual(len(snapshot["active_campaigns"]), 2)

    async def test_existing_corrupted_singleton_fails_fast_instead_of_being_accepted(self):
        await self.repo.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE bot_instances SET symbol = 'R_75', strategy_config = '{\"period\": 9}' WHERE id = ?",
                (NEXUS_TRADE_BOT_ID,),
            )
            await db.commit()

        with self.assertRaises(NexusTradeSingletonError):
            await self.repo.init_db()

    async def test_schema_reserves_nexus_identity_for_insert_and_update(self):
        await self.repo.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO bot_instances (id, name, strategy_id, account_id) VALUES ('nexus-trade', 'Other', 'donchian', '')"
                )
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "UPDATE bot_instances SET strategy_id = 'donchian' WHERE id = 'nexus-trade'"
                )
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "UPDATE bot_instances SET strategy_id = 'nexus_trade' WHERE id != 'nexus-trade'"
                )

    async def test_nexus_foreign_keys_and_lane_checks_reject_invalid_references(self):
        await self.repo.init_db()
        version_id = (await self.nexus.get_runtime_snapshot())["lanes"][0]["version"]["id"]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO nexus_runtime (bot_id, champion_version_id) VALUES ('missing', 'missing')"
                )
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO nexus_campaigns (id, lane, nexus_version_id, status) VALUES ('bad', 'invalid', ?, 'ACTIVE')",
                    (version_id,),
                )

    async def test_existing_database_upgrades_to_the_protected_nexus_schema(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(DatabaseModels.create_tables_sql())
            await db.execute(
                """
                CREATE TABLE nexus_tick_segments (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, start_epoch INTEGER NOT NULL,
                    end_epoch INTEGER NOT NULL, tick_count INTEGER NOT NULL,
                    byte_count INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

        await self.repo.init_db()

        snapshot = await self.nexus.get_runtime_snapshot()
        self.assertEqual(snapshot["bot"]["id"], "nexus-trade")
        async with aiosqlite.connect(self.db_path) as db:
            columns = {row[1] for row in await (await db.execute("PRAGMA table_info(nexus_tick_segments)")).fetchall()}
            self.assertIn("segment_sequence", columns)
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.execute(
                    "INSERT INTO bot_instances (id, name, strategy_id, account_id) VALUES ('nexus-trade', 'Other', 'donchian', '')"
                )

    async def test_populated_legacy_tick_manifest_rebuilds_contiguous_not_null_sequences_per_symbol(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(DatabaseModels.create_tables_sql())
            await db.execute(
                """
                CREATE TABLE nexus_tick_segments (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, start_epoch INTEGER NOT NULL,
                    end_epoch INTEGER NOT NULL, tick_count INTEGER NOT NULL,
                    byte_count INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.executemany(
                """
                INSERT INTO nexus_tick_segments
                    (id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("r75-early", "R_75", 10, 11, 2, 20, "a" * 64, "/legacy/r75-early", "2026-01-01 00:00:00"),
                    ("r100-early", "R_100", 20, 21, 2, 21, "b" * 64, "/legacy/r100-early", "2026-01-01 00:00:01"),
                    ("r75-late", "R_75", 30, 31, 2, 22, "c" * 64, "/legacy/r75-late", "2026-01-01 00:00:02"),
                    ("r100-late", "R_100", 40, 41, 2, 23, "d" * 64, "/legacy/r100-late", "2026-01-01 00:00:03"),
                ],
            )
            await db.commit()

        await self.repo.init_db()
        await self.repo.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            columns = await (await db.execute("PRAGMA table_info(nexus_tick_segments)")).fetchall()
            sequence_column = next(row for row in columns if row[1] == "segment_sequence")
            rows = await (await db.execute(
                "SELECT id, symbol, start_epoch, end_epoch, tick_count, byte_count, path, segment_sequence "
                "FROM nexus_tick_segments ORDER BY symbol, segment_sequence"
            )).fetchall()
        self.assertEqual(sequence_column[2:4], ("INTEGER", 1))
        self.assertEqual(
            rows,
            [
                ("r100-early", "R_100", 20, 21, 2, 21, "/legacy/r100-early", 1),
                ("r100-late", "R_100", 40, 41, 2, 23, "/legacy/r100-late", 2),
                ("r75-early", "R_75", 10, 11, 2, 20, "/legacy/r75-early", 1),
                ("r75-late", "R_75", 30, 31, 2, 22, "/legacy/r75-late", 2),
            ],
        )
        async with aiosqlite.connect(self.db_path) as db:
            indexes = await (await db.execute("PRAGMA index_list(nexus_tick_segments)")).fetchall()
            sequence_index = next(row for row in indexes if row[1] == "ux_nexus_tick_segments_symbol_sequence")
            sequence_columns = await (await db.execute(
                "PRAGMA index_info(ux_nexus_tick_segments_symbol_sequence)"
            )).fetchall()
            self.assertEqual(sequence_index[2], 1)
            self.assertEqual([row[2] for row in sequence_columns], ["symbol", "segment_sequence"])
            conflicts = [
                ("duplicate-sha", "R_100", 50, 51, 2, 25, "b" * 64, "/legacy/new-sha", 3),
                ("duplicate-path", "R_100", 50, 51, 2, 25, "e" * 64, "/legacy/r100-early", 3),
                ("duplicate-sequence", "R_100", 50, 51, 2, 25, "f" * 64, "/legacy/new-sequence", 1),
            ]
            for conflict in conflicts:
                with self.subTest(conflict=conflict[0]), self.assertRaises(aiosqlite.IntegrityError):
                    await db.execute(
                        """
                        INSERT INTO nexus_tick_segments
                            (id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, segment_sequence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        conflict,
                    )

    async def test_corrupted_champion_identity_fails_with_domain_error(self):
        await self.repo.init_db()
        version = (await self.nexus.get_runtime_snapshot())["lanes"][0]["version"]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE nexus_versions SET name = 'Corrupted', version_hash = 'wrong-hash' WHERE id = ?",
                (version["id"],),
            )
            await db.commit()

        with self.assertRaises(NexusTradeSingletonError):
            await self.repo.init_db()

    async def test_journal_migrations_reject_invalid_nexus_values_on_insert_and_update(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        version_id = snapshot["lanes"][0]["version"]["id"]
        campaign_id = snapshot["active_campaigns"][0]["id"]
        invalid_values = {
            "lane": "invalid-lane",
            "entry_delay_ms": -1,
            "nexus_version_id": "missing-version",
            "campaign_id": "missing-campaign",
            "decision_id": "missing-decision",
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                INSERT INTO nexus_decisions (id, lane, nexus_version_id, campaign_id, symbol, signal_epoch)
                VALUES ('decision-ok', 'champion_baseline', ?, ?, 'R_100', 1)
                """,
                (version_id, campaign_id),
            )
            valid_nexus = {
                "lane": "champion_baseline",
                "nexus_version_id": version_id,
                "campaign_id": campaign_id,
                "decision_id": "decision-ok",
                "entry_delay_ms": 0,
            }
            for journal in ("trades", "order_intents"):
                for field, invalid_value in invalid_values.items():
                    with self.subTest(journal=journal, operation="insert", field=field):
                        values = {**valid_nexus, field: invalid_value}
                        if journal == "trades":
                            columns = ", ".join(values)
                            placeholders = ", ".join("?" for _ in values)
                            with self.assertRaises(aiosqlite.IntegrityError):
                                await db.execute(
                                    f"INSERT INTO trades ({columns}) VALUES ({placeholders})",
                                    tuple(values.values()),
                                )
                        else:
                            columns = [
                                "id", "bot_id", "account_id", "proposal_id", "symbol",
                                "contract_type", "stake", "price", "duration", "duration_unit",
                                *values.keys(),
                            ]
                            parameters = [
                                f"insert-{field}", "nexus-trade", "demo", "proposal", "R_100",
                                "CALL", 0.35, 0.35, 58, "s", *values.values(),
                            ]
                            with self.assertRaises(aiosqlite.IntegrityError):
                                await db.execute(
                                    f"INSERT INTO order_intents ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                                    parameters,
                                )

                    with self.subTest(journal=journal, operation="update", field=field):
                        if journal == "trades":
                            cursor = await db.execute(
                                "INSERT INTO trades (lane, nexus_version_id, campaign_id, decision_id, entry_delay_ms) VALUES (?, ?, ?, ?, ?)",
                                tuple(valid_nexus.values()),
                            )
                            identity_column, identity_value = "id", cursor.lastrowid
                        else:
                            identity_column, identity_value = "id", f"update-{field}"
                            await db.execute(
                                """
                                INSERT INTO order_intents (
                                    id, bot_id, account_id, proposal_id, symbol, contract_type, stake, price,
                                    duration, duration_unit, lane, nexus_version_id, campaign_id, decision_id, entry_delay_ms
                                ) VALUES (?, 'nexus-trade', ?, 'proposal', 'R_100', 'CALL', 0.35, 0.35, 58, 's', ?, ?, ?, ?, ?)
                                """,
                                (identity_value, f"demo-{field}", *valid_nexus.values()),
                            )
                        with self.assertRaises(aiosqlite.IntegrityError):
                            await db.execute(
                                f"UPDATE {journal} SET {field} = ? WHERE {identity_column} = ?",
                                (invalid_value, identity_value),
                            )

    async def test_champion_management_defaults_persist_and_use_revision_cas(self):
        await self.repo.init_db()

        default = await self.nexus.get_champion_management()
        self.assertEqual(
            default,
            {
                "revision": 1,
                "initial_stake": 0.35,
                "money_management": "fixed",
                "money_config": {},
                "risk_config": {},
            },
        )

        updated = await self.nexus.set_champion_management(
            expected_revision=1,
            payload=self._champion_management_payload(),
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["money_management"], "martingale")
        self.assertEqual(updated["money_config"]["multiplier"], 2.1)

        restarted = NexusTradeRepository(self.db_path)
        self.assertEqual(await restarted.get_champion_management(), updated)
        with self.assertRaisesRegex(RuntimeError, "revision"):
            await restarted.set_champion_management(
                expected_revision=1,
                payload=self._champion_management_payload(initial_stake=0.9),
            )
        self.assertEqual(await restarted.get_champion_management(), updated)

        snapshot = await restarted.get_control_snapshot()
        self.assertEqual(snapshot["champion_management"], updated)

    async def test_champion_management_rejects_invalid_values_and_unsafe_lane(self):
        await self.repo.init_db()
        invalid_payloads = (
            self._champion_management_payload(initial_stake=float("nan")),
            self._champion_management_payload(initial_stake=float("inf")),
            self._champion_management_payload(initial_stake=-0.1),
            self._champion_management_payload(money_management="unknown"),
            self._champion_management_payload(
                money_management="martingale",
                money_config={"multiplier": 1.0, "max_levels": 3},
            ),
            self._champion_management_payload(
                risk_config={
                    **self._champion_management_payload()["risk_config"],
                    "max_single_stake": 0.1,
                },
            ),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await self.nexus.set_champion_management(
                    expected_revision=1,
                    payload=payload,
                )

        snapshot = await self.nexus.get_runtime_snapshot()
        champion = next(
            item for item in snapshot["lanes"]
            if item["lane"] == Lane.CHAMPION.value
        )
        async with aiosqlite.connect(self.db_path) as db:
            payload = json.dumps({
                "state": {
                    "position_status": "ACTIVE",
                    "owner_decision_id": "decision-active",
                    "contract_id": 7001,
                },
                "owner": {"decision_id": "decision-active", "contract_id": 7001},
            })
            await db.execute(
                """
                INSERT INTO nexus_decisions (
                    id, lane, nexus_version_id, campaign_id, symbol,
                    signal_epoch, payload
                ) VALUES (?, ?, ?, NULL, 'R_100', 60, ?)
                """,
                (
                    "decision-active",
                    Lane.CHAMPION.value,
                    champion["version"]["id"],
                    payload,
                ),
            )
            await db.execute(
                "INSERT INTO nexus_lane_heads (lane, snapshot_id) VALUES (?, ?)",
                (Lane.CHAMPION.value, "decision-active"),
            )
            await db.commit()

        with self.assertRaisesRegex(RuntimeError, "IDLE"):
            await self.nexus.set_champion_management(
                expected_revision=1,
                payload=self._champion_management_payload(),
            )

    async def test_champion_management_update_rolls_back_with_snapshot_revision(self):
        await self.repo.init_db()
        before = await self.nexus.get_control_snapshot()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TRIGGER abort_nexus_management_snapshot_advance
                BEFORE UPDATE OF config_revision ON bot_instances
                WHEN NEW.id = 'nexus-trade'
                BEGIN SELECT RAISE(ABORT, 'forced management rollback'); END;
                """
            )
            await db.commit()

        with self.assertRaises(aiosqlite.IntegrityError):
            await self.nexus.set_champion_management(
                expected_revision=1,
                payload=self._champion_management_payload(),
            )

        after = await self.nexus.get_control_snapshot()
        self.assertEqual(after["champion_management"], before["champion_management"])
        self.assertEqual(after["snapshot_version"], before["snapshot_version"])

    async def test_control_snapshot_reconstructs_only_durable_active_positions(self):
        await self.repo.init_db()
        runtime = await self.nexus.get_runtime_snapshot()
        champion_version = next(
            item["version"]["id"] for item in runtime["lanes"]
            if item["lane"] == Lane.CHAMPION.value
        )
        active_state = {
            "position_status": "ACTIVE",
            "owner_decision_id": "position-owner",
            "contract_id": 8123,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO nexus_decisions (
                    id, lane, nexus_version_id, campaign_id, symbol,
                    signal_epoch, payload
                ) VALUES (?, ?, ?, NULL, 'R_100', 120, ?)
                """,
                (
                    "position-owner",
                    Lane.CHAMPION.value,
                    champion_version,
                    json.dumps({
                        "state": active_state,
                        "owner": {
                            "account_id": "redacted-by-snapshot",
                            "account_type": "demo",
                            "management_active": False,
                        },
                    }),
                ),
            )
            await db.execute(
                "INSERT INTO nexus_lane_heads (lane, snapshot_id) VALUES (?, ?)",
                (Lane.CHAMPION.value, "position-owner"),
            )
            await db.commit()

        snapshot = await self.nexus.get_control_snapshot()
        self.assertEqual(snapshot["lane_states"][Lane.CHAMPION.value], active_state)
        self.assertEqual(snapshot["lane_states"][Lane.TRIAL.value]["position_status"], "IDLE")
        self.assertEqual(snapshot["positions"], [{
            "lane": Lane.CHAMPION.value,
            "contract_id": 8123,
            "owner_decision_id": "position-owner",
            "status": "RECONCILING",
            "update_epoch": 120,
            "stake": None,
            "buy_price": None,
            "current_spot": None,
            "profit": None,
            "date_expiry": None,
        }])

    async def test_control_snapshot_exposes_only_flat_operational_decisions(self):
        """Internal lane snapshots and settlements must not duplicate the UI journal."""
        await self.repo.init_db()
        runtime = await self.nexus.get_runtime_snapshot()
        trial = next(
            item for item in runtime["lanes"]
            if item["lane"] == Lane.TRIAL.value
        )
        campaign = next(
            item for item in runtime["active_campaigns"]
            if item["lane"] == Lane.TRIAL.value
        )
        decision = {
            "id": "decision-operational",
            "decision_id": "decision-operational",
            "lane": Lane.TRIAL.value,
            "contract_type": "CALL",
            "reason_codes": ["central_cross_up"],
            "signal_epoch": 1_723_000_000,
            "target_epoch": 1_723_000_060,
            "adx": 18.25,
            "blocked_reason": None,
            "provenance_hash": NEXUS_PROVENANCE_HASH,
        }
        idle_state = {
            "position_status": "IDLE",
            "owner_decision_id": None,
            "contract_id": None,
        }
        await self.repo.record_nexus_decision(
            decision,
            nexus_version_id=trial["version"]["id"],
            campaign_id=campaign["id"],
            state=idle_state,
            owner=None,
        )
        self.assertTrue(await self.repo.save_nexus_lane_state(
            Lane.TRIAL.value, idle_state, None,
        ))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO nexus_decisions (
                    id,lane,nexus_version_id,campaign_id,symbol,signal_epoch,payload
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    "settlement:decision-operational:77", Lane.TRIAL.value,
                    trial["version"]["id"], campaign["id"], "R_100",
                    1_723_000_118,
                    json.dumps({
                        "decision": {"outcome": "SETTLED"},
                        "state": idle_state,
                        "settlement": {"contract_id": 77},
                    }),
                ),
            )
            await db.commit()

        snapshot = await self.nexus.get_control_snapshot()

        self.assertEqual(len(snapshot["decisions"]), 1)
        public = snapshot["decisions"][0]
        self.assertEqual(public["decision_id"], "decision-operational")
        self.assertEqual(public["contract_type"], "CALL")
        self.assertEqual(public["adx"], 18.25)
        self.assertEqual(public["nexus_version_id"], trial["version"]["id"])
        self.assertEqual(public["campaign_id"], campaign["id"])
        self.assertNotIn("payload", public)
        self.assertNotIn("account_id", json.dumps(snapshot["positions"]))

    async def test_control_snapshot_exposes_champion_session_semantics(self):
        await self.repo.init_db()
        payload = self._champion_management_payload(
            initial_stake=1.25,
            money_management="soros",
            money_config={"levels": 2, "percent": 0.6},
        )
        suggestion = await self.nexus.set_champion_management(
            expected_revision=1,
            payload=payload,
        )

        off_snapshot = await self.nexus.get_control_snapshot()
        self.assertEqual(
            off_snapshot["champion_session"],
            {
                "management_active": False,
                "mode": "off",
                "baseline_account_type": "demo",
                "baseline_initial_stake": 0.35,
                "suggestion": suggestion,
                "active_management": None,
            },
        )

        await self.nexus.set_champion_mode(
            enabled=True,
            account_id="DOT-DEMO",
            account_type="demo",
        )
        on_snapshot = await self.nexus.get_control_snapshot()
        self.assertEqual(on_snapshot["champion_session"]["management_active"], True)
        self.assertEqual(on_snapshot["champion_session"]["mode"], "on")
        self.assertEqual(
            on_snapshot["champion_session"]["suggestion"],
            suggestion,
        )
        self.assertEqual(
            on_snapshot["champion_session"]["active_management"],
            suggestion,
        )

    async def test_control_snapshot_reports_last_hour_accuracy_for_champion_only(self):
        await self.repo.init_db()
        snapshot = await self.nexus.get_runtime_snapshot()
        champion_version = snapshot["runtime"]["champion_version_id"]
        champion_campaign = next(
            item["id"]
            for item in snapshot["active_campaigns"]
            if item["lane"] == Lane.CHAMPION.value
        )
        trial_version = snapshot["runtime"]["trial_version_id"]
        trial_campaign = next(
            item["id"]
            for item in snapshot["active_campaigns"]
            if item["lane"] == Lane.TRIAL.value
        )
        now_epoch = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            for contract_id, lane, version_id, campaign_id, signal_epoch in (
                (9101, Lane.CHAMPION.value, champion_version, champion_campaign, now_epoch - 600),
                (9102, Lane.CHAMPION.value, champion_version, champion_campaign, now_epoch - 300),
                (9103, Lane.CHAMPION.value, champion_version, champion_campaign, now_epoch - 120),
                (9104, Lane.CHAMPION.value, champion_version, champion_campaign, now_epoch - 4000),
                (9201, Lane.TRIAL.value, trial_version, trial_campaign, now_epoch - 180),
            ):
                await db.execute(
                    """
                    INSERT INTO nexus_decisions (
                        id, lane, nexus_version_id, campaign_id, symbol, signal_epoch, payload
                    ) VALUES (?, ?, ?, ?, 'R_100', ?, ?)
                    """,
                    (
                        f"decision-{contract_id}",
                        lane,
                        version_id,
                        campaign_id,
                        signal_epoch,
                        json.dumps({
                            "decision": {
                                "id": f"decision-{contract_id}",
                                "decision_id": f"decision-{contract_id}",
                                "lane": lane,
                                "signal_epoch": signal_epoch,
                            },
                            "state": {"position_status": "IDLE"},
                            "owner": None,
                        }),
                    ),
                )
            await db.commit()

        trades = (
            (9101, Lane.CHAMPION.value, champion_version, champion_campaign, "won", now_epoch - 600, now_epoch - 542),
            (9102, Lane.CHAMPION.value, champion_version, champion_campaign, "lost", now_epoch - 300, now_epoch - 242),
            (9103, Lane.CHAMPION.value, champion_version, champion_campaign, "tie", now_epoch - 120, now_epoch - 62),
            (9104, Lane.CHAMPION.value, champion_version, champion_campaign, "won", now_epoch - 4000, now_epoch - 3942),
            (9201, Lane.TRIAL.value, trial_version, trial_campaign, "won", now_epoch - 180, now_epoch - 122),
        )
        for contract_id, lane, version_id, campaign_id, result, purchase_time, expiry_time in trades:
            await self.repo.upsert_trade({
                "bot_id": NEXUS_TRADE_BOT_ID,
                "strategy_name": "nexus_trade",
                "symbol": "R_100",
                "contract_type": "CALL",
                "contract_id": contract_id,
                "stake": 0.35,
                "payout": 0.66 if result != "lost" else 0.0,
                "profit": 0.31 if result == "won" else -0.35 if result == "lost" else 0.0,
                "result": result,
                "status": "closed",
                "purchase_time": purchase_time,
                "expiry_time": expiry_time,
                "lane": lane,
                "nexus_version_id": version_id,
                "campaign_id": campaign_id,
                "decision_id": f"decision-{contract_id}",
            })

        control = await self.nexus.get_control_snapshot()
        self.assertEqual(
            control["champion_last_hour"],
            {
                "window_seconds": 3600,
                "closed_trades": 3,
                "wins": 1,
                "losses": 1,
                "ties": 1,
                "decisive_trades": 2,
                "accuracy": 0.5,
            },
        )


if __name__ == "__main__":
    unittest.main()
