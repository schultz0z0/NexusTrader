import aiosqlite
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from config.settings import settings
from database.models import DatabaseModels
from database.nexus_models import NexusModels
from nexus_trade.repository import NexusTradeRepository
from risk.state import advance_risk_state, initial_risk_state
from utils.logger import setup_logger

logger = setup_logger("Database")


class ActiveOrderIntentError(RuntimeError):
    """The account is quarantined until its previous buy outcome is resolved."""


class DatabaseRepository:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.DB_PATH
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def init_db(self):
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=30000;")
            await db.executescript(DatabaseModels.create_tables_sql())
            await self._migrate_trade_columns(db)
            await self._migrate_nexus_tick_segments(db)
            await db.executescript(NexusModels.create_tables_sql())
            await db.executescript(NexusModels.create_journal_guards_sql())
            await db.execute("BEGIN IMMEDIATE")
            try:
                # Insere configuracao inicial de risco se tabela estiver vazia
                async with db.execute("SELECT COUNT(*) FROM risk_configs") as cursor:
                    count = (await cursor.fetchone())[0]
                    if count == 0:
                        await db.execute("""
                            INSERT INTO risk_configs (initial_stake, stop_loss_daily, take_profit_daily, max_daily_trades, max_single_stake, max_consecutive_losses, cooldown_minutes)
                            VALUES (1.0, 50.0, 100.0, 50, 20.0, 3, 15)
                        """)
                await self._ensure_default_bot(db)
                await self._backfill_risk_states(db)
                await NexusTradeRepository.ensure_singleton_in_transaction(db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        logger.info(f"Banco de dados SQLite '{self.db_path}' pronto.")

    async def _migrate_trade_columns(self, db):
        async with db.execute("PRAGMA table_info(trades)") as cursor:
            existing = {row[1] for row in await cursor.fetchall()}
        additions = {
            "bot_id": "TEXT",
            "status": "TEXT DEFAULT 'closed'",
            "entry_spot": "REAL",
            "exit_spot": "REAL",
            "purchase_time": "INTEGER",
            "expiry_time": "INTEGER",
            "risk_applied": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in additions.items():
            if column not in existing:
                await db.execute(f"ALTER TABLE trades ADD COLUMN {column} {definition}")
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trades_bot_contract
            ON trades(bot_id, contract_id)
            WHERE bot_id IS NOT NULL AND contract_id IS NOT NULL
        """)
        async with db.execute("PRAGMA table_info(order_intents)") as cursor:
            existing_intents = {row[1] for row in await cursor.fetchall()}
        nexus_additions = {
            "lane": "TEXT",
            "nexus_version_id": "TEXT",
            "campaign_id": "TEXT",
            "decision_id": "TEXT",
            "entry_delay_ms": "INTEGER",
        }
        for column, definition in nexus_additions.items():
            if column not in existing:
                await db.execute(f"ALTER TABLE trades ADD COLUMN {column} {definition}")
            if column not in existing_intents:
                await db.execute(f"ALTER TABLE order_intents ADD COLUMN {column} {definition}")

    async def _migrate_nexus_tick_segments(self, db):
        """Rebuild legacy manifests with NOT NULL per-symbol causal sequencing."""
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'nexus_tick_segments'"
        ) as cursor:
            if await cursor.fetchone() is None:
                return
        async with db.execute("PRAGMA table_info(nexus_tick_segments)") as cursor:
            columns = {row[1]: row for row in await cursor.fetchall()}
        sequence_column = columns.get("segment_sequence")
        needs_rebuild = (
            sequence_column is None
            or str(sequence_column[2]).upper() != "INTEGER"
            or int(sequence_column[3]) != 1
        )
        if not needs_rebuild:
            async with db.execute(
                "SELECT symbol, segment_sequence FROM nexus_tick_segments ORDER BY symbol, segment_sequence"
            ) as cursor:
                rows = await cursor.fetchall()
            expected_by_symbol = {}
            for symbol, sequence in rows:
                expected = expected_by_symbol.get(symbol, 0) + 1
                if type(sequence) is not int or sequence != expected:
                    needs_rebuild = True
                    break
                expected_by_symbol[symbol] = expected
        if not needs_rebuild:
            return
        await db.execute("SAVEPOINT rebuild_nexus_tick_segments")
        try:
            await db.execute(
                """
                CREATE TABLE nexus_tick_segments_rebuilt (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, start_epoch INTEGER NOT NULL,
                    end_epoch INTEGER NOT NULL, tick_count INTEGER NOT NULL,
                    byte_count INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE, segment_sequence INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            async with db.execute(
                """
                SELECT id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, created_at
                FROM nexus_tick_segments ORDER BY symbol, start_epoch, end_epoch, created_at, id
                """
            ) as cursor:
                rows = await cursor.fetchall()
            sequence_by_symbol = {}
            for row in rows:
                symbol = row[1]
                sequence_by_symbol[symbol] = sequence_by_symbol.get(symbol, 0) + 1
                await db.execute(
                    """
                    INSERT INTO nexus_tick_segments_rebuilt
                        (id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, segment_sequence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*row[:8], sequence_by_symbol[symbol], row[8]),
                )
            await db.execute("DROP TABLE nexus_tick_segments")
            await db.execute("ALTER TABLE nexus_tick_segments_rebuilt RENAME TO nexus_tick_segments")
            await db.execute("RELEASE SAVEPOINT rebuild_nexus_tick_segments")
        except Exception:
            await db.execute("ROLLBACK TO SAVEPOINT rebuild_nexus_tick_segments")
            await db.execute("RELEASE SAVEPOINT rebuild_nexus_tick_segments")
            raise

    async def _ensure_default_bot(self, db):
        async with db.execute("SELECT COUNT(*) FROM bot_instances") as cursor:
            if (await cursor.fetchone())[0] > 0:
                return
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_settings ORDER BY id DESC LIMIT 1") as cursor:
            legacy = await cursor.fetchone()
        async with db.execute("SELECT * FROM risk_configs ORDER BY id DESC LIMIT 1") as cursor:
            risk = await cursor.fetchone()
        legacy_data = dict(legacy) if legacy else {}
        risk_data = dict(risk) if risk else {
            "initial_stake": 1.0,
            "stop_loss_daily": 50.0,
            "take_profit_daily": 100.0,
            "max_daily_trades": 50,
            "max_single_stake": 20.0,
            "max_consecutive_losses": 3,
            "cooldown_minutes": 15,
        }
        await db.execute("""
            INSERT INTO bot_instances (
                id, name, strategy_id, strategy_config, account_id, account_type,
                symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                money_management, money_config, risk_config, desired_state, runtime_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STOPPED', 'STOPPED')
        """, (
            str(uuid.uuid4()),
            "Donchian",
            "donchian",
            json.dumps({}),
            legacy_data.get("account_id", ""),
            legacy_data.get("account_type", "demo"),
            legacy_data.get("symbol", "R_75"),
            60,
            2,
            "m",
            risk_data.get("initial_stake", 1.0),
            "martingale",
            json.dumps({"multiplier": 2.0, "max_levels": risk_data.get("max_consecutive_losses", 3)}),
            json.dumps({key: value for key, value in risk_data.items() if key not in {"id", "updated_at"}}),
        ))

    async def _backfill_risk_states(self, db):
        """Upgrade legacy journals once without changing already-versioned snapshots."""
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT bot.* FROM bot_instances AS bot
            LEFT JOIN risk_states AS risk ON risk.bot_id = bot.id
            WHERE risk.bot_id IS NULL
        """) as cursor:
            bots = [self._decode_bot(row) for row in await cursor.fetchall()]
        for bot in bots:
            state = initial_risk_state(float(bot.get("initial_stake", 1.0)))
            async with db.execute("""
                SELECT * FROM trades
                WHERE bot_id = ? AND status = 'closed'
                ORDER BY COALESCE(expiry_time, purchase_time, 0), id
            """, (bot["id"],)) as cursor:
                trades = [dict(row) for row in await cursor.fetchall()]
            for trade in trades:
                state = advance_risk_state(
                    state,
                    is_win=(
                        str(trade.get("result") or "").lower() == "won"
                        or float(trade.get("profit") or 0) > 0
                    ),
                    profit=float(trade.get("profit") or 0),
                    mode=bot.get("money_management", "fixed"),
                    initial_stake=float(bot.get("initial_stake", 1.0)),
                    money_config=bot.get("money_config") or {},
                    risk_config=bot.get("risk_config") or {},
                    settled_epoch=float(
                        trade.get("expiry_time") or trade.get("purchase_time") or 0
                    ),
                )
            await db.execute("""
                INSERT INTO risk_states (
                    bot_id, current_stake, current_level, consecutive_wins,
                    consecutive_losses, circuit_consecutive_losses,
                    circuit_tripped_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                bot["id"], state["current_stake"], state["current_level"],
                state["consecutive_wins"], state["consecutive_losses"],
                state["circuit_consecutive_losses"], state["circuit_tripped_at"],
            ))
            await db.execute("""
                UPDATE trades SET risk_applied = 1
                WHERE bot_id = ? AND status = 'closed'
            """, (bot["id"],))

    @staticmethod
    def _decode_bot(row):
        if row is None:
            return None
        data = dict(row)
        for field in ("strategy_config", "money_config", "risk_config"):
            raw = data.get(field)
            data[field] = json.loads(raw) if isinstance(raw, str) and raw else {}
        return data

    async def get_risk_config(self) -> dict:
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM risk_configs ORDER BY id DESC LIMIT 1") as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return {
                    "initial_stake": 1.0,
                    "stop_loss_daily": 50.0,
                    "take_profit_daily": 100.0,
                    "max_daily_trades": 50,
                    "max_single_stake": 20.0,
                    "max_consecutive_losses": 3,
                    "cooldown_minutes": 15
                }

    async def update_risk_config(self, config: dict):
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""
                INSERT INTO risk_configs (initial_stake, stop_loss_daily, take_profit_daily, max_daily_trades, max_single_stake, max_consecutive_losses, cooldown_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                config.get("initial_stake", 1.0),
                config.get("stop_loss_daily", 50.0),
                config.get("take_profit_daily", 100.0),
                config.get("max_daily_trades", 50),
                config.get("max_single_stake", 20.0),
                config.get("max_consecutive_losses", 3),
                config.get("cooldown_minutes", 15)
            ))
            await db.commit()
            logger.info("Configuracao de risco atualizada dinamicamente no BD.")

    async def get_bot_settings(self) -> dict:
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_settings ORDER BY id DESC LIMIT 1") as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return {
                    "account_id": "",
                    "account_type": "demo",
                    "symbol": "R_100",
                    "strategy": "BollingerBands(20, 2.0)"
                }

    async def update_bot_settings(self, settings: dict):
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""
                INSERT INTO bot_settings (account_id, account_type, symbol, strategy)
                VALUES (?, ?, ?, ?)
            """, (
                settings.get("account_id"),
                settings.get("account_type"),
                settings.get("symbol"),
                settings.get("strategy", "BollingerBands(20, 2.0)")
            ))
            await db.commit()
            logger.info("Configuracao do robo atualizada dinamicamente no BD.")

    async def create_session(self, session_id: str):
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute(
                "INSERT INTO sessions (id) VALUES (?)",
                (session_id,)
            )
            await db.commit()

    async def close_session(self, session_id: str, status: str = "closed"):
        async with self._connection() as db:
            await db.execute(
                "UPDATE sessions SET end_time = CURRENT_TIMESTAMP, status = ? WHERE id = ?",
                (status, session_id),
            )
            await db.commit()

    async def save_trade(self, trade_data: dict):
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""
                INSERT INTO trades (session_id, strategy_name, symbol, contract_type, contract_id, stake, payout, profit, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get('session_id'),
                trade_data.get('strategy_name'),
                trade_data.get('symbol'),
                trade_data.get('contract_type'),
                trade_data.get('contract_id'),
                trade_data.get('stake'),
                trade_data.get('payout'),
                trade_data.get('profit'),
                trade_data.get('result')
            ))
            await db.commit()

    async def list_bots(self) -> list:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_instances ORDER BY created_at, name") as cursor:
                return [self._decode_bot(row) for row in await cursor.fetchall()]

    async def get_bot(self, bot_id: str):
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_instances WHERE id = ?", (bot_id,)) as cursor:
                return self._decode_bot(await cursor.fetchone())

    async def delete_bot(self, bot_id: str):
        async with self._connection() as db:
            await db.execute("DELETE FROM bot_instances WHERE id = ?", (bot_id,))
            await db.commit()

    async def get_default_bot(self):
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_instances ORDER BY created_at LIMIT 1") as cursor:
                return self._decode_bot(await cursor.fetchone())

    async def create_bot(self, data: dict) -> dict:
        bot_id = data.get("id") or str(uuid.uuid4())
        values = (
            bot_id,
            data.get("name", "Novo Robo"),
            data.get("strategy_id", "donchian"),
            json.dumps(data.get("strategy_config", {})),
            data.get("account_id", ""),
            data.get("account_type", "demo"),
            data.get("symbol", "R_75"),
            int(data.get("timeframe_seconds", 60)),
            int(data.get("duration", 2)),
            data.get("duration_unit", "m"),
            float(data.get("initial_stake", 1.0)),
            data.get("money_management", "fixed"),
            json.dumps(data.get("money_config", {})),
            json.dumps(data.get("risk_config", {})),
            data.get("desired_state", "STOPPED"),
            data.get("runtime_state", "STOPPED"),
        )
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("""
                INSERT INTO bot_instances (
                    id, name, strategy_id, strategy_config, account_id, account_type,
                    symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                    money_management, money_config, risk_config, desired_state, runtime_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            await db.commit()
        return await self.get_bot(bot_id)

    async def update_bot(self, bot_id: str, data: dict):
        allowed = {
            "name", "strategy_id", "account_id", "account_type", "symbol",
            "timeframe_seconds", "duration", "duration_unit", "initial_stake",
            "money_management", "desired_state", "runtime_state", "last_error",
        }
        json_fields = {"strategy_config", "money_config", "risk_config"}
        assignments = []
        values = []
        for key, value in data.items():
            if key in allowed or key in json_fields:
                assignments.append(f"{key} = ?")
                values.append(json.dumps(value) if key in json_fields else value)
        if not assignments:
            return await self.get_bot(bot_id)
        assignments.extend(["config_revision = config_revision + 1", "updated_at = CURRENT_TIMESTAMP"])
        values.append(bot_id)
        async with self._connection() as db:
            await db.execute(
                f"UPDATE bot_instances SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            await db.commit()
        return await self.get_bot(bot_id)

    async def set_desired_state(self, bot_id: str, state: str):
        return await self.update_bot(bot_id, {"desired_state": state})

    async def stop_all_bots(self) -> int:
        async with self._connection() as db:
            cursor = await db.execute("""
                UPDATE bot_instances
                SET desired_state = 'STOPPED', config_revision = config_revision + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE desired_state != 'STOPPED'
            """)
            await db.commit()
            return max(0, int(cursor.rowcount))

    async def set_runtime_state(self, bot_id: str, state: str, error: str = None):
        async with self._connection() as db:
            await db.execute("""
                UPDATE bot_instances
                SET runtime_state = ?, last_error = ?, heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (state, error, bot_id))
            await db.commit()
        return await self.get_bot(bot_id)

    async def touch_bot_heartbeat(self, bot_id: str):
        async with self._connection() as db:
            await db.execute(
                "UPDATE bot_instances SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?",
                (bot_id,),
            )
            await db.commit()

    async def upsert_trade(self, trade_data: dict):
        columns = (
            "bot_id", "session_id", "strategy_name", "symbol", "contract_type",
            "contract_id", "stake", "payout", "profit", "result", "status",
            "entry_spot", "exit_spot", "purchase_time", "expiry_time",
            "lane", "nexus_version_id", "campaign_id", "decision_id",
            "entry_delay_ms",
        )
        values = [trade_data.get(column) for column in columns]
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            updated = 0
            if trade_data.get("bot_id") and trade_data.get("contract_id") is not None:
                cursor = await db.execute("""
                    UPDATE trades SET
                        session_id = ?, strategy_name = ?, symbol = ?, contract_type = ?,
                        stake = ?, payout = ?, profit = ?, result = ?, status = ?,
                        entry_spot = ?, exit_spot = ?, purchase_time = ?, expiry_time = ?,
                        lane = ?, nexus_version_id = ?, campaign_id = ?, decision_id = ?,
                        entry_delay_ms = ?
                    WHERE bot_id = ? AND contract_id = ?
                """, (
                    trade_data.get("session_id"), trade_data.get("strategy_name"),
                    trade_data.get("symbol"), trade_data.get("contract_type"),
                    trade_data.get("stake"), trade_data.get("payout"),
                    trade_data.get("profit"), trade_data.get("result"),
                    trade_data.get("status"), trade_data.get("entry_spot"),
                    trade_data.get("exit_spot"), trade_data.get("purchase_time"),
                    trade_data.get("expiry_time"), trade_data.get("lane"),
                    trade_data.get("nexus_version_id"), trade_data.get("campaign_id"),
                    trade_data.get("decision_id"), trade_data.get("entry_delay_ms"),
                    trade_data.get("bot_id"),
                    trade_data.get("contract_id"),
                ))
                updated = cursor.rowcount
            if not updated:
                placeholders = ", ".join("?" for _ in columns)
                await db.execute(
                    f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            await db.commit()

    async def list_trades(self, bot_id: str = None, limit: int = 100) -> list:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM trades"
        params = []
        if bot_id:
            query += " WHERE bot_id = ?"
            params.append(bot_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_bot_daily_stats(self, bot_id: str):
        business_zone = ZoneInfo(settings.BUSINESS_TIMEZONE)
        local_today = datetime.now(business_zone).date()
        start_local = datetime.combine(local_today, time.min, tzinfo=business_zone)
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT COALESCE(SUM(profit), 0.0), COUNT(*)
                FROM trades
                WHERE bot_id = ? AND status = 'closed'
                  AND created_at >= ? AND created_at < ?
                """,
                (bot_id, start_utc, end_utc),
            ) as cursor:
                row = await cursor.fetchone()
        return float(row[0]), int(row[1])

    async def get_daily_stats(self) -> dict:
        """Calcula PnL e quantidade de trades do dia atual."""
        async with self._connection() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            async with db.execute("""
                SELECT 
                    COALESCE(SUM(profit), 0.0) as total_profit,
                    COUNT(*) as total_trades
                FROM trades
                WHERE DATE(created_at) = DATE('now')
            """) as cursor:
                row = await cursor.fetchone()
                return {
                    "daily_pnl": float(row[0]),
                    "daily_trades": int(row[1])
                }

    @staticmethod
    def _decode_order_intent(row):
        if row is None:
            return None
        data = dict(row)
        raw_metadata = data.get("metadata")
        data["metadata"] = json.loads(raw_metadata) if raw_metadata else {}
        return data

    @staticmethod
    def _merge_order_intent_metadata(existing: dict = None, external: dict = None) -> dict:
        """Merge journal metadata without allowing ownership identity to drift."""
        stored = dict(existing or {})
        incoming = dict(external or {})
        merged = {**stored, **incoming}
        protected = (
            "correlation_id",
            "order_intent_id",
            "decision_id",
            "account_id",
            "account_type",
            "management_active",
        )
        for key in protected:
            if key in stored:
                merged[key] = stored[key]

        stored_entry = stored.get("entry_intent")
        incoming_entry = incoming.get("entry_intent")
        if isinstance(stored_entry, dict) or isinstance(incoming_entry, dict):
            entry = {
                **(stored_entry if isinstance(stored_entry, dict) else {}),
                **(incoming_entry if isinstance(incoming_entry, dict) else {}),
            }
            for key in ("decision_id", "lane"):
                if isinstance(stored_entry, dict) and key in stored_entry:
                    entry[key] = stored_entry[key]
            merged["entry_intent"] = entry
        return merged

    @staticmethod
    def _normalize_nexus_owner(owner: dict = None) -> dict | None:
        if owner is None:
            return None
        account_id = str(owner.get("account_id") or "").strip()
        account_type = str(owner.get("account_type") or "").lower()
        management_active = owner.get("management_active")
        if not account_id or account_type not in {"demo", "real"}:
            raise ValueError("Nexus lane owner requires an exact account identity")
        if type(management_active) is not bool:
            raise TypeError("Nexus lane owner management_active must be boolean")
        return {
            "account_id": account_id,
            "account_type": account_type,
            "management_active": management_active,
        }

    async def create_order_intent(self, data: dict) -> dict:
        intent_id = data.get("id") or str(uuid.uuid4())
        values = (
            intent_id,
            data["bot_id"],
            data["account_id"],
            data.get("session_id"),
            data["proposal_id"],
            data["symbol"],
            data["contract_type"],
            float(data["stake"]),
            float(data["price"]),
            int(data["duration"]),
            data["duration_unit"],
            int(data.get("signal_epoch") or 0),
            json.dumps(data.get("metadata") or {}),
            data.get("lane"),
            data.get("nexus_version_id"),
            data.get("campaign_id"),
            data.get("decision_id"),
            data.get("entry_delay_ms"),
        )
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("""
                    INSERT INTO order_intents (
                        id, bot_id, account_id, session_id, proposal_id, symbol,
                        contract_type, stake, price, duration, duration_unit,
                        signal_epoch, metadata, lane, nexus_version_id,
                        campaign_id, decision_id, entry_delay_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, values)
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                if "order_intents.account_id" in str(exc):
                    raise ActiveOrderIntentError(
                        f"A conta {data['account_id']} possui uma compra sem ownership confirmado"
                    ) from exc
                raise
        return await self.get_order_intent(intent_id)

    async def prepare_nexus_order_intent(
        self,
        intent_id: str,
        *,
        proposal_id: str,
        price: float,
        metadata: dict,
    ) -> dict:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT metadata FROM order_intents WHERE id = ? AND state = 'prepared'",
                (intent_id,),
            ) as cursor:
                row = await cursor.fetchone()
            merged = self._merge_order_intent_metadata(
                json.loads(row["metadata"] or "{}") if row is not None else {},
                metadata,
            )
            await db.execute(
                """
                UPDATE order_intents
                SET proposal_id = ?, price = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = 'prepared'
                """,
                (proposal_id, float(price), json.dumps(merged), intent_id),
            )
            await db.commit()
        return await self.get_order_intent(intent_id)

    async def finalize_nexus_order_intent(
        self,
        intent_id: str,
        *,
        entry_delay_ms: int,
        metadata: dict,
    ) -> dict:
        if isinstance(entry_delay_ms, bool) or type(entry_delay_ms) is not int or entry_delay_ms < 0:
            raise ValueError("entry_delay_ms must be a non-negative integer")
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT metadata FROM order_intents WHERE id = ? AND state = 'owned'",
                (intent_id,),
            ) as cursor:
                row = await cursor.fetchone()
            merged = self._merge_order_intent_metadata(
                json.loads(row["metadata"] or "{}") if row is not None else {},
                metadata,
            )
            await db.execute(
                """
                UPDATE order_intents
                SET entry_delay_ms = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = 'owned'
                """,
                (entry_delay_ms, json.dumps(merged), intent_id),
            )
            await db.commit()
        return await self.get_order_intent(intent_id)

    async def commit_nexus_known_ownership(
        self,
        intent_id: str,
        contract: dict,
        *,
        entry_intent: dict,
        entry_delay_ms: int,
    ) -> dict:
        """Atomically own a contract and move its Nexus lane to ACTIVE."""
        contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
        if isinstance(contract_id, bool) or type(contract_id) is not int or contract_id <= 0:
            raise ValueError("contract_id must be a positive integer")
        if isinstance(entry_delay_ms, bool) or type(entry_delay_ms) is not int or entry_delay_ms < 0:
            raise ValueError("entry_delay_ms must be a non-negative integer")
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT * FROM order_intents WHERE id = ?", (intent_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise ValueError("Nexus ownership intent does not exist")
                stored = self._decode_order_intent(row)
                lane = stored.get("lane")
                decision_id = stored.get("decision_id")
                if not lane or not decision_id:
                    raise ValueError("Nexus ownership requires lane and decision_id")
                async with db.execute(
                    "SELECT id FROM order_intents WHERE contract_id = ? AND id != ?",
                    (contract_id, intent_id),
                ) as cursor:
                    collision = await cursor.fetchone()
                if collision is not None:
                    raise ValueError(
                        f"contract_id {contract_id} is already owned by {collision['id']}",
                    )
                metadata = self._merge_order_intent_metadata(
                    stored.get("metadata") or {},
                    {**dict(contract), "entry_intent": dict(entry_intent)},
                )
                transaction_id = contract.get("transaction_id")
                await db.execute(
                    """
                    UPDATE order_intents
                    SET state = 'owned', contract_id = ?, transaction_id = ?,
                        entry_delay_ms = ?, error = NULL, metadata = ?,
                        resolved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        contract_id,
                        int(transaction_id) if transaction_id is not None else None,
                        entry_delay_ms,
                        json.dumps(metadata),
                        intent_id,
                    ),
                )
                async with db.execute(
                    "SELECT payload FROM nexus_decisions WHERE id = ? AND lane = ?",
                    (decision_id, lane),
                ) as cursor:
                    decision = await cursor.fetchone()
                if decision is None:
                    raise ValueError("durable RESERVED Nexus decision was not found")
                payload = json.loads(decision["payload"])
                owner = self._normalize_nexus_owner({
                    key: metadata.get(key)
                    for key in ("account_id", "account_type", "management_active")
                })
                durable_owner = self._normalize_nexus_owner(payload.get("owner"))
                if durable_owner is not None and durable_owner != owner:
                    raise ValueError("Nexus ownership identity conflicts with lane journal")
                payload["owner"] = owner
                state = dict(payload.get("state") or {})
                state.update(
                    position_status="ACTIVE",
                    owner_decision_id=decision_id,
                    contract_id=contract_id,
                    quarantine_correlation_id=None,
                )
                payload["state"] = state
                await db.execute(
                    "UPDATE nexus_decisions SET entry_delay_ms = ?, payload = ? WHERE id = ?",
                    (
                        entry_delay_ms,
                        json.dumps(payload, sort_keys=True, separators=(",", ":")),
                        decision_id,
                    ),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return await self.get_order_intent(intent_id)

    async def record_nexus_decision(
        self,
        decision: dict,
        *,
        nexus_version_id: str,
        campaign_id: str = None,
        state: dict,
        owner: dict = None,
    ) -> None:
        payload = {
            "decision": dict(decision),
            "state": dict(state),
            "owner": self._normalize_nexus_owner(owner),
        }
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO nexus_decisions (
                    id, lane, nexus_version_id, campaign_id, symbol,
                    signal_epoch, entry_delay_ms, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    decision.get("id") or decision["decision_id"],
                    decision["lane"],
                    nexus_version_id,
                    campaign_id,
                    "R_100",
                    int(decision["signal_epoch"]),
                    None,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
            await db.commit()

    async def save_nexus_lane_state(
        self, lane: str, state: dict, owner: dict = None,
    ) -> None:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, payload FROM nexus_decisions WHERE lane = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (lane,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return
            payload = json.loads(row["payload"])
            payload["state"] = dict(state)
            payload["owner"] = self._normalize_nexus_owner(owner)
            await db.execute(
                "UPDATE nexus_decisions SET payload = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), row["id"]),
            )
            await db.commit()

    async def load_nexus_lane_states(self) -> dict:
        states = {}
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            for lane in ("champion_baseline", "challenger_trial"):
                async with db.execute(
                    "SELECT payload FROM nexus_decisions WHERE lane = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (lane,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is not None:
                    payload = json.loads(row["payload"])
                    if isinstance(payload.get("state"), dict):
                        states[lane] = payload["state"]
        return states

    async def load_nexus_lane_owners(self) -> dict:
        owners = {}
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            for lane in ("champion_baseline", "challenger_trial"):
                async with db.execute(
                    "SELECT payload FROM nexus_decisions WHERE lane = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (lane,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is not None:
                    payload = json.loads(row["payload"] or "{}")
                    owners[lane] = self._normalize_nexus_owner(
                        payload.get("owner"),
                    )
        return owners

    async def get_nexus_runtime_snapshot(self) -> dict:
        return await NexusTradeRepository(self.db_path).get_runtime_snapshot()

    async def get_order_intent(self, intent_id: str):
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM order_intents WHERE id = ?", (intent_id,)
            ) as cursor:
                return self._decode_order_intent(await cursor.fetchone())

    async def list_unresolved_order_intents(
        self, bot_id: str = None, account_id: str = None
    ) -> list:
        query = """
            SELECT * FROM order_intents
            WHERE state IN ('prepared', 'submitting', 'reconcile_pending', 'ambiguous')
        """
        params = []
        if bot_id:
            query += " AND bot_id = ?"
            params.append(bot_id)
        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)
        query += " ORDER BY created_at"
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [self._decode_order_intent(row) for row in await cursor.fetchall()]

    async def list_owned_intents_without_trade(self, bot_id: str) -> list:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT intent.*
                FROM order_intents AS intent
                LEFT JOIN trades AS trade
                  ON trade.bot_id = intent.bot_id
                 AND trade.contract_id = intent.contract_id
                WHERE intent.bot_id = ? AND intent.state = 'owned'
                  AND intent.contract_id IS NOT NULL AND trade.id IS NULL
                ORDER BY intent.created_at
            """, (bot_id,)) as cursor:
                return [
                    self._decode_order_intent(row) for row in await cursor.fetchall()
                ]

    async def list_nexus_recovery_intents(self, bot_id: str) -> list:
        """Return Nexus lifecycle journals needed to recover RESERVED lanes."""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM order_intents
                WHERE bot_id = ? AND lane IS NOT NULL AND decision_id IS NOT NULL
                  AND state IN (
                      'prepared', 'submitting', 'reconcile_pending',
                      'ambiguous', 'owned'
                  )
                ORDER BY created_at, id
                """,
                (bot_id,),
            ) as cursor:
                return [
                    self._decode_order_intent(row) for row in await cursor.fetchall()
                ]

    async def list_order_intents(self, bot_id: str, limit=100) -> list:
        limit = max(1, min(int(limit), 1000))
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM order_intents
                WHERE bot_id = ? ORDER BY created_at DESC LIMIT ?
            """, (bot_id, limit)) as cursor:
                return [
                    self._decode_order_intent(row) for row in await cursor.fetchall()
                ]

    async def update_order_intent(
        self,
        intent_id: str,
        state: str,
        *,
        error: str = None,
        metadata: dict = None,
    ) -> dict:
        terminal = state in {"owned", "rejected", "cancelled"}
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            merged = None
            if metadata is not None:
                async with db.execute(
                    "SELECT metadata FROM order_intents WHERE id = ?", (intent_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                merged = self._merge_order_intent_metadata(
                    json.loads(row["metadata"] or "{}") if row is not None else {},
                    metadata,
                )
            await db.execute("""
                UPDATE order_intents
                SET state = ?, error = ?,
                    metadata = COALESCE(?, metadata),
                    resolved_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                state,
                error,
                json.dumps(merged) if merged is not None else None,
                1 if terminal else 0,
                intent_id,
            ))
            await db.commit()
        return await self.get_order_intent(intent_id)

    async def mark_order_intent_owned(self, intent_id: str, contract: dict) -> dict:
        contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
        if type(contract_id) is not int or contract_id <= 0:
            raise ValueError("contract_id must be a positive integer")
        transaction_id = contract.get("transaction_id")
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT metadata FROM order_intents WHERE id = ?", (intent_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise ValueError("order intent does not exist")
                metadata = self._merge_order_intent_metadata(
                    json.loads(row["metadata"] or "{}"),
                    dict(contract),
                )
                await db.execute("""
                    UPDATE order_intents
                    SET state = 'owned', contract_id = ?, transaction_id = ?, error = NULL,
                        metadata = ?, resolved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    contract_id,
                    int(transaction_id) if transaction_id is not None else None,
                    json.dumps(metadata),
                    intent_id,
                ))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return await self.get_order_intent(intent_id)

    @staticmethod
    def _decode_risk_state(row, initial_stake=1.0):
        if row is None:
            return initial_risk_state(initial_stake)
        data = dict(row)
        return {
            "current_stake": float(data["current_stake"]),
            "current_level": int(data["current_level"]),
            "consecutive_wins": int(data["consecutive_wins"]),
            "consecutive_losses": int(data["consecutive_losses"]),
            "circuit_consecutive_losses": int(data["circuit_consecutive_losses"]),
            "circuit_tripped_at": float(data["circuit_tripped_at"]),
        }

    async def get_risk_state(self, bot_id: str, initial_stake=1.0) -> dict:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM risk_states WHERE bot_id = ?", (bot_id,)
            ) as cursor:
                return self._decode_risk_state(await cursor.fetchone(), initial_stake)

    async def settle_trade_and_risk(
        self,
        trade_data: dict,
        *,
        money_management: str,
        money_config: dict,
        risk_config: dict,
        initial_stake: float,
        settled_epoch: float,
    ) -> dict:
        bot_id = trade_data["bot_id"]
        contract_id = int(trade_data["contract_id"])
        columns = (
            "bot_id", "session_id", "strategy_name", "symbol", "contract_type",
            "contract_id", "stake", "payout", "profit", "result", "status",
            "entry_spot", "exit_spot", "purchase_time", "expiry_time",
        )
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT risk_applied FROM trades WHERE bot_id = ? AND contract_id = ?",
                (bot_id, contract_id),
            ) as cursor:
                existing_trade = await cursor.fetchone()
            async with db.execute(
                "SELECT * FROM risk_states WHERE bot_id = ?", (bot_id,)
            ) as cursor:
                stored_state = self._decode_risk_state(
                    await cursor.fetchone(), initial_stake
                )
            if existing_trade and int(existing_trade["risk_applied"] or 0) == 1:
                await db.commit()
                return {"applied": False, "state": stored_state}

            updated = await db.execute("""
                UPDATE trades SET
                    session_id = ?, strategy_name = ?, symbol = ?, contract_type = ?,
                    stake = ?, payout = ?, profit = ?, result = ?, status = ?,
                    entry_spot = ?, exit_spot = ?, purchase_time = ?, expiry_time = ?
                WHERE bot_id = ? AND contract_id = ?
            """, (
                trade_data.get("session_id"), trade_data.get("strategy_name"),
                trade_data.get("symbol"), trade_data.get("contract_type"),
                trade_data.get("stake"), trade_data.get("payout"),
                trade_data.get("profit"), trade_data.get("result"),
                trade_data.get("status"), trade_data.get("entry_spot"),
                trade_data.get("exit_spot"), trade_data.get("purchase_time"),
                trade_data.get("expiry_time"), bot_id, contract_id,
            ))
            if not updated.rowcount:
                placeholders = ", ".join("?" for _ in columns)
                await db.execute(
                    f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders})",
                    [trade_data.get(column) for column in columns],
                )

            next_state = advance_risk_state(
                stored_state,
                is_win=(
                    str(trade_data.get("result") or "").lower() == "won"
                    or float(trade_data.get("profit") or 0) > 0
                ),
                profit=float(trade_data.get("profit") or 0),
                mode=money_management,
                initial_stake=float(initial_stake),
                money_config=money_config or {},
                risk_config=risk_config or {},
                settled_epoch=float(settled_epoch),
            )
            await db.execute("""
                INSERT INTO risk_states (
                    bot_id, current_stake, current_level, consecutive_wins,
                    consecutive_losses, circuit_consecutive_losses,
                    circuit_tripped_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bot_id) DO UPDATE SET
                    current_stake = excluded.current_stake,
                    current_level = excluded.current_level,
                    consecutive_wins = excluded.consecutive_wins,
                    consecutive_losses = excluded.consecutive_losses,
                    circuit_consecutive_losses = excluded.circuit_consecutive_losses,
                    circuit_tripped_at = excluded.circuit_tripped_at,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                bot_id,
                next_state["current_stake"],
                next_state["current_level"],
                next_state["consecutive_wins"],
                next_state["consecutive_losses"],
                next_state["circuit_consecutive_losses"],
                next_state["circuit_tripped_at"],
            ))
            await db.execute(
                "UPDATE trades SET risk_applied = 1 WHERE bot_id = ? AND contract_id = ?",
                (bot_id, contract_id),
            )
            await db.commit()
        return {"applied": True, "state": next_state}

    async def settle_nexus_trade_and_lane(
        self,
        trade_data: dict,
        *,
        lane_state: dict,
        apply_risk: bool,
        money_management: str,
        money_config: dict,
        risk_config: dict,
        initial_stake: float,
        settled_epoch: float,
        owner: dict = None,
    ) -> dict:
        """Commit Nexus trade, optional risk, ownership journal and IDLE lane together."""
        if type(apply_risk) is not bool:
            raise TypeError("apply_risk must be boolean")
        if lane_state.get("position_status") != "IDLE":
            raise ValueError("settled Nexus lane state must be IDLE")
        bot_id = trade_data["bot_id"]
        contract_id = int(trade_data["contract_id"])
        lane = trade_data["lane"]
        decision_id = trade_data["decision_id"]
        columns = (
            "bot_id", "session_id", "strategy_name", "symbol", "contract_type",
            "contract_id", "stake", "payout", "profit", "result", "status",
            "entry_spot", "exit_spot", "purchase_time", "expiry_time",
            "lane", "nexus_version_id", "campaign_id", "decision_id",
            "entry_delay_ms",
        )
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT risk_applied FROM trades WHERE bot_id = ? AND contract_id = ?",
                    (bot_id, contract_id),
                ) as cursor:
                    existing_trade = await cursor.fetchone()
                updated = await db.execute(
                    """
                    UPDATE trades SET
                        session_id = ?, strategy_name = ?, symbol = ?, contract_type = ?,
                        stake = ?, payout = ?, profit = ?, result = ?, status = ?,
                        entry_spot = ?, exit_spot = ?, purchase_time = ?, expiry_time = ?,
                        lane = ?, nexus_version_id = ?, campaign_id = ?, decision_id = ?,
                        entry_delay_ms = ?
                    WHERE bot_id = ? AND contract_id = ?
                    """,
                    (
                        trade_data.get("session_id"), trade_data.get("strategy_name"),
                        trade_data.get("symbol"), trade_data.get("contract_type"),
                        trade_data.get("stake"), trade_data.get("payout"),
                        trade_data.get("profit"), trade_data.get("result"),
                        trade_data.get("status"), trade_data.get("entry_spot"),
                        trade_data.get("exit_spot"), trade_data.get("purchase_time"),
                        trade_data.get("expiry_time"), lane,
                        trade_data.get("nexus_version_id"),
                        trade_data.get("campaign_id"), decision_id,
                        trade_data.get("entry_delay_ms"), bot_id, contract_id,
                    ),
                )
                if not updated.rowcount:
                    placeholders = ", ".join("?" for _ in columns)
                    await db.execute(
                        f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders})",
                        [trade_data.get(column) for column in columns],
                    )

                next_state = None
                risk_applied = False
                if apply_risk:
                    async with db.execute(
                        "SELECT * FROM risk_states WHERE bot_id = ?", (bot_id,),
                    ) as cursor:
                        stored_state = self._decode_risk_state(
                            await cursor.fetchone(), initial_stake,
                        )
                    already_applied = (
                        existing_trade is not None
                        and int(existing_trade["risk_applied"] or 0) == 1
                    )
                    if already_applied:
                        next_state = stored_state
                    else:
                        next_state = advance_risk_state(
                            stored_state,
                            is_win=(
                                str(trade_data.get("result") or "").lower() == "won"
                                or float(trade_data.get("profit") or 0) > 0
                            ),
                            profit=float(trade_data.get("profit") or 0),
                            mode=money_management,
                            initial_stake=float(initial_stake),
                            money_config=money_config or {},
                            risk_config=risk_config or {},
                            settled_epoch=float(settled_epoch),
                        )
                        await db.execute(
                            """
                            INSERT INTO risk_states (
                                bot_id, current_stake, current_level,
                                consecutive_wins, consecutive_losses,
                                circuit_consecutive_losses, circuit_tripped_at,
                                updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(bot_id) DO UPDATE SET
                                current_stake = excluded.current_stake,
                                current_level = excluded.current_level,
                                consecutive_wins = excluded.consecutive_wins,
                                consecutive_losses = excluded.consecutive_losses,
                                circuit_consecutive_losses = excluded.circuit_consecutive_losses,
                                circuit_tripped_at = excluded.circuit_tripped_at,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (
                                bot_id, next_state["current_stake"],
                                next_state["current_level"],
                                next_state["consecutive_wins"],
                                next_state["consecutive_losses"],
                                next_state["circuit_consecutive_losses"],
                                next_state["circuit_tripped_at"],
                            ),
                        )
                        await db.execute(
                            "UPDATE trades SET risk_applied = 1 WHERE bot_id = ? AND contract_id = ?",
                            (bot_id, contract_id),
                        )
                        risk_applied = True

                async with db.execute(
                    """
                    SELECT payload, nexus_version_id, campaign_id, symbol
                    FROM nexus_decisions WHERE id = ? AND lane = ?
                    """,
                    (decision_id, lane),
                ) as cursor:
                    decision = await cursor.fetchone()
                if decision is None:
                    raise ValueError("settlement Nexus decision journal was not found")
                payload = json.loads(decision["payload"])
                durable_owner = self._normalize_nexus_owner(payload.get("owner"))
                expected_owner = self._normalize_nexus_owner(owner)
                if expected_owner is not None and durable_owner != expected_owner:
                    raise ValueError("settlement owner does not match durable lane owner")
                payload["state"] = dict(lane_state)
                payload["owner"] = None
                await db.execute(
                    "UPDATE nexus_decisions SET payload = ? WHERE id = ?",
                    (
                        json.dumps(payload, sort_keys=True, separators=(",", ":")),
                        decision_id,
                    ),
                )
                settlement_id = f"settlement:{decision_id}:{contract_id}"
                settlement_payload = {
                    "decision": {
                        "id": settlement_id,
                        "decision_id": settlement_id,
                        "lane": lane,
                        "signal_epoch": int(settled_epoch),
                        "owner_decision_id": decision_id,
                        "outcome": "SETTLED",
                    },
                    "state": dict(lane_state),
                    "owner": None,
                    "settlement": {
                        "owner_decision_id": decision_id,
                        "contract_id": contract_id,
                        "result": trade_data.get("result"),
                        "profit": trade_data.get("profit"),
                    },
                }
                await db.execute(
                    """
                    INSERT INTO nexus_decisions (
                        id, lane, nexus_version_id, campaign_id, symbol,
                        signal_epoch, entry_delay_ms, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        entry_delay_ms = excluded.entry_delay_ms,
                        payload = excluded.payload
                    """,
                    (
                        settlement_id,
                        lane,
                        trade_data.get("nexus_version_id")
                        or decision["nexus_version_id"],
                        (
                            trade_data.get("campaign_id")
                            if "campaign_id" in trade_data
                            else decision["campaign_id"]
                        ),
                        trade_data.get("symbol") or decision["symbol"] or "R_100",
                        int(settled_epoch),
                        trade_data.get("entry_delay_ms"),
                        json.dumps(
                            settlement_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                intent_id = f"nexus-{decision_id}"
                async with db.execute(
                    "SELECT metadata FROM order_intents WHERE id = ? AND contract_id = ?",
                    (intent_id, contract_id),
                ) as cursor:
                    intent = await cursor.fetchone()
                if intent is None:
                    raise ValueError("settlement ownership journal was not found")
                metadata = json.loads(intent["metadata"] or "{}")
                metadata["settlement"] = {
                    "contract_id": contract_id,
                    "result": trade_data.get("result"),
                    "profit": trade_data.get("profit"),
                }
                await db.execute(
                    """
                    UPDATE order_intents
                    SET state = 'settled', metadata = ?, resolved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND contract_id = ?
                    """,
                    (json.dumps(metadata), intent_id, contract_id),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return {"applied": risk_applied, "state": next_state}

    async def record_bot_health(
        self,
        bot_id: str,
        *,
        deriv_connected: bool,
        publisher_healthy: bool,
        market_epoch: int = None,
    ):
        async with self._connection() as db:
            await db.execute("""
                INSERT INTO runtime_health (
                    bot_id, deriv_connected, publisher_healthy, market_epoch, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bot_id) DO UPDATE SET
                    deriv_connected = excluded.deriv_connected,
                    publisher_healthy = excluded.publisher_healthy,
                    market_epoch = excluded.market_epoch,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                bot_id,
                1 if deriv_connected else 0,
                1 if publisher_healthy else 0,
                int(market_epoch) if market_epoch else None,
            ))
            await db.commit()

    async def record_service_heartbeat(self, service_name: str, details=None):
        async with self._connection() as db:
            await db.execute("""
                INSERT INTO service_heartbeats (service_name, details, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(service_name) DO UPDATE SET
                    details = excluded.details, updated_at = CURRENT_TIMESTAMP
            """, (service_name, json.dumps(details or {})))
            await db.commit()

    async def readiness(self) -> dict:
        checks = {"database": "ok", "orchestrator": "not_required", "bots": {}}
        heartbeat_limit = max(15, int(settings.RUNTIME_HEARTBEAT_SECONDS) * 3)
        market_limit = max(
            int(settings.MARKET_STALE_AFTER_SECONDS),
            int(settings.RUNTIME_HEARTBEAT_SECONDS) * 3,
        )
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, runtime_state,
                    CAST(strftime('%s','now') - strftime('%s', heartbeat_at) AS INTEGER)
                        AS heartbeat_age
                FROM bot_instances
                WHERE desired_state = 'RUNNING'
            """) as cursor:
                running = [dict(row) for row in await cursor.fetchall()]
            if not running:
                return {"ready": True, "checks": checks}

            async with db.execute("""
                SELECT CAST(strftime('%s','now') - strftime('%s', updated_at) AS INTEGER)
                    AS age
                FROM service_heartbeats WHERE service_name = 'orchestrator'
            """) as cursor:
                service = await cursor.fetchone()
            service_age = service["age"] if service else None
            orchestrator_ok = service_age is not None and service_age <= heartbeat_limit
            checks["orchestrator"] = "ok" if orchestrator_ok else "stale"

            ready = orchestrator_ok
            for bot in running:
                async with db.execute("""
                    SELECT COUNT(*) FROM order_intents
                    WHERE bot_id = ?
                      AND state IN ('prepared', 'submitting', 'reconcile_pending', 'ambiguous')
                """, (bot["id"],)) as cursor:
                    unresolved_intents = int((await cursor.fetchone())[0])
                async with db.execute("""
                    SELECT deriv_connected, publisher_healthy, market_epoch,
                        CAST(strftime('%s','now') - strftime('%s', updated_at) AS INTEGER)
                            AS health_age,
                        CAST(strftime('%s','now') AS INTEGER) - market_epoch AS market_age
                    FROM runtime_health WHERE bot_id = ?
                """, (bot["id"],)) as cursor:
                    health = await cursor.fetchone()
                status = {
                    "runtime": bot["runtime_state"],
                    "heartbeat": "ok" if (
                        bot["heartbeat_age"] is not None
                        and bot["heartbeat_age"] <= heartbeat_limit
                    ) else "stale",
                    "deriv": "ok" if health and health["deriv_connected"] else "disconnected",
                    "publisher": "ok" if health and health["publisher_healthy"] else "unhealthy",
                    "market": "ok" if (
                        health
                        and health["market_age"] is not None
                        and health["market_age"] <= market_limit
                    ) else "stale",
                    "ownership": "ok" if unresolved_intents == 0 else "quarantined",
                }
                bot_ok = (
                    status["runtime"] == "RUNNING"
                    and all(value == "ok" for key, value in status.items() if key != "runtime")
                    and health
                    and health["health_age"] is not None
                    and health["health_age"] <= heartbeat_limit
                )
                status["ready"] = bool(bot_ok)
                checks["bots"][bot["id"]] = status
                ready = ready and bool(bot_ok)
        return {"ready": bool(ready), "checks": checks}
