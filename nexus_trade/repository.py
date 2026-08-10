import hashlib
import json
from contextlib import asynccontextmanager

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

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

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
        async with db.execute(
            "SELECT * FROM bot_instances WHERE id = ? OR strategy_id = 'nexus_trade'",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            existing_bots = await cursor.fetchall()
        if any(row["id"] != NEXUS_TRADE_BOT_ID or row["strategy_id"] != "nexus_trade" for row in existing_bots):
            raise NexusTradeSingletonError("NexusTrade must use the protected nexus-trade identity")
        if existing_bots:
            cls._validate_canonical_bot(existing_bots[0])

        cursor = await db.execute(
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
        async with db.execute(
            "SELECT * FROM nexus_versions WHERE id = ? OR version_hash = ?",
            (version_id, version_hash),
        ) as version_cursor:
            versions = await version_cursor.fetchall()
        if versions:
            if len(versions) != 1:
                raise NexusTradeSingletonError("Champion V1 identity is ambiguous")
            cls._validate_champion_v1(versions[0], version_id, version_hash, encoded_snapshot)
        else:
            await db.execute(
                """
                INSERT INTO nexus_versions (id, name, status, version_hash, snapshot)
                VALUES (?, 'Champion V1', ?, ?, ?)
                """,
                (version_id, VersionStatus.CHAMPION.value, version_hash, encoded_snapshot),
            )

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
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.executescript(NexusModels.create_tables_sql())
            await db.execute("BEGIN IMMEDIATE")
            try:
                snapshot = await self.ensure_singleton_in_transaction(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    def _validate_canonical_bot(bot: aiosqlite.Row) -> None:
        expected = {
            "id": NEXUS_TRADE_BOT_ID,
            "name": "NexusTrade",
            "strategy_id": "nexus_trade",
            "strategy_config": "{}",
            "account_id": "",
            "account_type": "demo",
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "duration": NEXUS_DURATION_SECONDS,
            "duration_unit": NEXUS_DURATION_UNIT,
            "initial_stake": NEXUS_DEMO_STAKE,
            "money_management": "fixed",
            "money_config": "{}",
            "risk_config": "{}",
        }
        mismatches = [key for key, value in expected.items() if bot[key] != value]
        if mismatches:
            raise NexusTradeSingletonError(
                f"NexusTrade singleton has incompatible fields: {', '.join(mismatches)}"
            )

    @staticmethod
    def _validate_champion_v1(
        version: aiosqlite.Row,
        version_id: str,
        version_hash: str,
        snapshot: str,
    ) -> None:
        expected = {
            "id": version_id,
            "name": "Champion V1",
            "status": VersionStatus.CHAMPION.value,
            "version_hash": version_hash,
            "snapshot": snapshot,
        }
        mismatches = [key for key, value in expected.items() if version[key] != value]
        if mismatches:
            raise NexusTradeSingletonError(
                f"Champion V1 has incompatible fields: {', '.join(mismatches)}"
            )

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
        async with self._connection() as db:
            return await self._snapshot_from_connection(db)

    async def set_champion_mode(
        self, *, enabled: bool, account_id: str, account_type: str,
    ) -> dict:
        if type(enabled) is not bool:
            raise TypeError("enabled must be boolean")
        normalized_account_id = str(account_id or "").strip()
        normalized_account_type = str(account_type or "").lower()
        if normalized_account_type not in {"demo", "real"}:
            raise ValueError("Champion account type must be demo or real")
        if enabled and not normalized_account_id:
            raise ValueError("Champion ON requires a selected account")
        if not enabled and normalized_account_type != "demo":
            raise ValueError("Champion OFF must remain on DEMO")
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    UPDATE nexus_runtime
                    SET champion_enabled = ?, champion_account_id = ?,
                        champion_account_type = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE bot_id = ?
                    """,
                    (
                        int(enabled),
                        normalized_account_id,
                        normalized_account_type,
                        NEXUS_TRADE_BOT_ID,
                    ),
                )
                await self._advance_snapshot_version(db)
                snapshot = await self._snapshot_from_connection(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    async def set_emergency_stop(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise TypeError("emergency_stop must be boolean")
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    UPDATE nexus_runtime
                    SET emergency_stop = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE bot_id = ?
                    """,
                    (int(enabled), NEXUS_TRADE_BOT_ID),
                )
                await self._advance_snapshot_version(db)
                snapshot = await self._snapshot_from_connection(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    async def _advance_snapshot_version(db: aiosqlite.Connection) -> None:
        cursor = await db.execute(
            """
            UPDATE bot_instances
            SET config_revision = config_revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND strategy_id = 'nexus_trade'
            """,
            (NEXUS_TRADE_BOT_ID,),
        )
        if cursor.rowcount != 1:
            raise NexusTradeSingletonError("NexusTrade singleton is unavailable")

    async def get_control_snapshot(self) -> dict:
        durable = await self.get_runtime_snapshot()
        decisions = await self._list_json_rows(
            """
            SELECT * FROM nexus_decisions
            ORDER BY created_at DESC, id DESC LIMIT 100
            """,
            json_fields=("payload",),
        )
        trades = await self._list_json_rows(
            """
            SELECT * FROM trades WHERE bot_id = ?
            ORDER BY id DESC LIMIT 100
            """,
            params=(NEXUS_TRADE_BOT_ID,),
            json_fields=("metadata",),
        )
        runtime = durable["runtime"]
        return {
            "schema_version": 1,
            "snapshot_version": int(durable["bot"].get("config_revision") or 1),
            "bot_id": NEXUS_TRADE_BOT_ID,
            "runtime": runtime,
            "emergency_stop": bool(runtime.get("emergency_stop", 0)),
            "lanes": durable["lanes"],
            "active_campaigns": durable["active_campaigns"],
            "decisions": decisions,
            "trades": trades,
            "reports": await self.list_reports(),
            "proposals": await self.list_proposals(),
        }

    async def list_versions(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_versions ORDER BY created_at, id",
            json_fields=("snapshot",),
        )

    async def list_campaigns(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_campaigns ORDER BY started_at, id",
        )

    async def list_reports(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_reports ORDER BY created_at DESC, id DESC",
            json_fields=("snapshot",),
        )

    async def list_proposals(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_proposals ORDER BY created_at DESC, id DESC",
            json_fields=("payload",),
        )

    async def _list_json_rows(
        self, query: str, *, params=(), json_fields=(),
    ) -> list:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
        for row in rows:
            for field in json_fields:
                raw_value = row.get(field)
                if raw_value is not None:
                    row[field] = json.loads(raw_value)
        return rows
