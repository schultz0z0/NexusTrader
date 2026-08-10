import aiosqlite
import json
import os
import uuid
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

    async def init_db(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=30000;")
            await db.executescript(DatabaseModels.create_tables_sql())
            await self._migrate_trade_columns(db)
            await db.executescript(NexusModels.create_tables_sql())
            
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute(
                "INSERT INTO sessions (id) VALUES (?)",
                (session_id,)
            )
            await db.commit()

    async def close_session(self, session_id: str, status: str = "closed"):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute(
                "UPDATE sessions SET end_time = CURRENT_TIMESTAMP, status = ? WHERE id = ?",
                (status, session_id),
            )
            await db.commit()

    async def save_trade(self, trade_data: dict):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_instances ORDER BY created_at, name") as cursor:
                return [self._decode_bot(row) for row in await cursor.fetchall()]

    async def get_bot(self, bot_id: str):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM bot_instances WHERE id = ?", (bot_id,)) as cursor:
                return self._decode_bot(await cursor.fetchone())

    async def delete_bot(self, bot_id: str):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("DELETE FROM bot_instances WHERE id = ?", (bot_id,))
            await db.commit()

    async def get_default_bot(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute(
                f"UPDATE bot_instances SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            await db.commit()
        return await self.get_bot(bot_id)

    async def set_desired_state(self, bot_id: str, state: str):
        return await self.update_bot(bot_id, {"desired_state": state})

    async def stop_all_bots(self) -> int:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            cursor = await db.execute("""
                UPDATE bot_instances
                SET desired_state = 'STOPPED', config_revision = config_revision + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE desired_state != 'STOPPED'
            """)
            await db.commit()
            return max(0, int(cursor.rowcount))

    async def set_runtime_state(self, bot_id: str, state: str, error: str = None):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("""
                UPDATE bot_instances
                SET runtime_state = ?, last_error = ?, heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (state, error, bot_id))
            await db.commit()
        return await self.get_bot(bot_id)

    async def touch_bot_heartbeat(self, bot_id: str):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        )
        values = [trade_data.get(column) for column in columns]
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            updated = 0
            if trade_data.get("bot_id") and trade_data.get("contract_id") is not None:
                cursor = await db.execute("""
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
                    trade_data.get("expiry_time"), trade_data.get("bot_id"),
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        )
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("""
                    INSERT INTO order_intents (
                        id, bot_id, account_id, session_id, proposal_id, symbol,
                        contract_type, stake, price, duration, duration_unit,
                        signal_epoch, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    async def get_order_intent(self, intent_id: str):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [self._decode_order_intent(row) for row in await cursor.fetchall()]

    async def list_owned_intents_without_trade(self, bot_id: str) -> list:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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

    async def list_order_intents(self, bot_id: str, limit=100) -> list:
        limit = max(1, min(int(limit), 1000))
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
                json.dumps(metadata) if metadata is not None else None,
                1 if terminal else 0,
                intent_id,
            ))
            await db.commit()
        return await self.get_order_intent(intent_id)

    async def mark_order_intent_owned(self, intent_id: str, contract: dict) -> dict:
        contract_id = int(contract["contract_id"])
        transaction_id = contract.get("transaction_id")
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("""
                UPDATE order_intents
                SET state = 'owned', contract_id = ?, transaction_id = ?, error = NULL,
                    metadata = ?, resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                contract_id,
                int(transaction_id) if transaction_id is not None else None,
                json.dumps(contract),
                intent_id,
            ))
            await db.commit()
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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

    async def record_bot_health(
        self,
        bot_id: str,
        *,
        deriv_connected: bool,
        publisher_healthy: bool,
        market_epoch: int = None,
    ):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
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
