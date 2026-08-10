import asyncio
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from database.repository import DatabaseRepository
from database.models import DatabaseModels
from nexus_trade.constants import NEXUS_TRADE_BOT_ID
from nexus_trade.domain import CampaignStatus, Lane, VersionStatus
from nexus_trade.repository import NexusTradeRepository, NexusTradeSingletonError


class NexusTradeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "nexus-trade.db")
        self.repo = DatabaseRepository(self.db_path)
        self.nexus = NexusTradeRepository(self.db_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

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
        self.assertEqual(champion["version"]["status"], VersionStatus.CHAMPION.value)
        self.assertEqual(champion["version"]["name"], "Champion V1")
        self.assertEqual(champion["version"]["snapshot"]["bollinger"], {"period": 20, "std_dev": 2, "ma": "SMA"})
        self.assertEqual(champion["version"]["snapshot"]["adx"], {"period": 14, "max_entry": 22})
        self.assertEqual(len(snapshot["active_campaigns"]), 1)
        self.assertEqual(snapshot["active_campaigns"][0]["lane"], Lane.TRIAL.value)
        self.assertEqual(snapshot["active_campaigns"][0]["status"], CampaignStatus.ACTIVE.value)

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
        self.assertEqual(len(snapshot["active_campaigns"]), 1)

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


if __name__ == "__main__":
    unittest.main()
