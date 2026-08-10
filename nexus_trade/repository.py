import hashlib
import json

import aiosqlite

from database.nexus_models import NexusModels
from nexus_trade.constants import (
    NEXUS_DEMO_STAKE,
    NEXUS_DURATION_SECONDS,
    NEXUS_DURATION_UNIT,
    NEXUS_SYMBOL,
    NEXUS_TIMEFRAME_SECONDS,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.domain import CampaignStatus, Lane, VersionStatus


class NexusTradeSingletonError(RuntimeError):
    """Raised when the protected NexusTrade identity has been corrupted."""


class NexusTradeRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @staticmethod
    def champion_v1_snapshot() -> dict:
        return {
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "duration_seconds": NEXUS_DURATION_SECONDS,
            "bollinger": {"period": 20, "std_dev": 2, "ma": "SMA"},
            "adx": {"period": 14, "max_entry": 22},
        }

    @classmethod
    async def ensure_singleton_in_transaction(cls, db: aiosqlite.Connection) -> dict:
        db.row_factory = aiosqlite.Row
        await db.executescript(NexusModels.create_tables_sql())
        async with db.execute(
            "SELECT id, strategy_id FROM bot_instances WHERE id = ? OR strategy_id = 'nexus_trade'",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            existing_bots = await cursor.fetchall()
        if any(row["id"] != NEXUS_TRADE_BOT_ID or row["strategy_id"] != "nexus_trade" for row in existing_bots):
            raise NexusTradeSingletonError("NexusTrade must use the protected nexus-trade identity")

        await db.execute(
            """
            INSERT INTO bot_instances (
                id, name, strategy_id, strategy_config, account_id, account_type,
                symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                money_management, money_config, risk_config, desired_state, runtime_state
            ) VALUES (?, 'NexusTrade', 'nexus_trade', '{}', '', 'demo', ?, ?, ?, ?, ?, 'fixed', '{}', '{}', 'STOPPED', 'STOPPED')
            ON CONFLICT(id) DO NOTHING
            """,
            (NEXUS_TRADE_BOT_ID, NEXUS_SYMBOL, NEXUS_TIMEFRAME_SECONDS,
             NEXUS_DURATION_SECONDS, NEXUS_DURATION_UNIT, NEXUS_DEMO_STAKE),
        )

        snapshot = cls.champion_v1_snapshot()
        encoded_snapshot = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        version_hash = hashlib.sha256(encoded_snapshot.encode("utf-8")).hexdigest()
        version_id = f"nexus-v1-{version_hash[:12]}"
        await db.execute(
            """
            INSERT INTO nexus_versions (id, name, status, version_hash, snapshot)
            VALUES (?, 'Champion V1', ?, ?, ?)
            ON CONFLICT(version_hash) DO NOTHING
            """,
            (version_id, VersionStatus.CHAMPION.value, version_hash, encoded_snapshot),
        )
        async with db.execute("SELECT id FROM nexus_versions WHERE version_hash = ?", (version_hash,)) as cursor:
            version_id = (await cursor.fetchone())["id"]

        await db.execute(
            """
            INSERT INTO nexus_runtime (bot_id, champion_version_id, trial_version_id)
            VALUES (?, ?, ?)
            ON CONFLICT(bot_id) DO NOTHING
            """,
            (NEXUS_TRADE_BOT_ID, version_id, version_id),
        )
        await db.execute(
            """
            INSERT INTO nexus_campaigns (id, lane, nexus_version_id, status)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM nexus_campaigns WHERE lane = ? AND status = ?
            )
            """,
            (f"trial-{version_id}", Lane.TRIAL.value, version_id, CampaignStatus.ACTIVE.value,
             Lane.TRIAL.value, CampaignStatus.ACTIVE.value),
        )
        return await cls._snapshot_from_connection(db)

    async def ensure_singleton(self) -> dict:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                snapshot = await self.ensure_singleton_in_transaction(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    @classmethod
    async def _snapshot_from_connection(cls, db: aiosqlite.Connection) -> dict:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_instances WHERE id = ?", (NEXUS_TRADE_BOT_ID,)) as cursor:
            bot = await cursor.fetchone()
        if bot is None:
            raise NexusTradeSingletonError("NexusTrade singleton has not been provisioned")
        async with db.execute("SELECT * FROM nexus_runtime WHERE bot_id = ?", (NEXUS_TRADE_BOT_ID,)) as cursor:
            runtime = await cursor.fetchone()
        if runtime is None:
            raise NexusTradeSingletonError("NexusTrade runtime pointers are missing")

        lanes = []
        for lane, pointer in ((Lane.CHAMPION, "champion_version_id"), (Lane.TRIAL, "trial_version_id")):
            version_id = runtime[pointer]
            async with db.execute("SELECT * FROM nexus_versions WHERE id = ?", (version_id,)) as cursor:
                version = await cursor.fetchone()
            version_data = dict(version)
            version_data["snapshot"] = json.loads(version_data.pop("snapshot"))
            lanes.append({"lane": lane.value, "version": version_data})
        async with db.execute(
            "SELECT * FROM nexus_campaigns WHERE lane = ? AND status = ? ORDER BY started_at, id",
            (Lane.TRIAL.value, CampaignStatus.ACTIVE.value),
        ) as cursor:
            active_campaigns = [dict(row) for row in await cursor.fetchall()]
        return {"bot": dict(bot), "runtime": dict(runtime), "lanes": lanes, "active_campaigns": active_campaigns}

    async def get_runtime_snapshot(self) -> dict:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            return await self._snapshot_from_connection(db)
