import aiosqlite
import json
import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config.settings import settings
from database.models import DatabaseModels
from utils.logger import setup_logger

logger = setup_logger("Database")

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
            
            # Insere configuracao inicial de risco se tabela estiver vazia
            async with db.execute("SELECT COUNT(*) FROM risk_configs") as cursor:
                count = (await cursor.fetchone())[0]
                if count == 0:
                    await db.execute("""
                        INSERT INTO risk_configs (initial_stake, stop_loss_daily, take_profit_daily, max_daily_trades, max_single_stake, max_consecutive_losses, cooldown_minutes)
                        VALUES (1.0, 50.0, 100.0, 50, 20.0, 3, 15)
                    """)
            await self._ensure_default_bot(db)
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
        }
        for column, definition in additions.items():
            if column not in existing:
                await db.execute(f"ALTER TABLE trades ADD COLUMN {column} {definition}")
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trades_bot_contract
            ON trades(bot_id, contract_id)
            WHERE bot_id IS NOT NULL AND contract_id IS NOT NULL
        """)

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
