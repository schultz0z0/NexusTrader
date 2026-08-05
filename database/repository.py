import aiosqlite
import os
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

    async def _connect(self):
        db = await aiosqlite.connect(self.db_path, timeout=30.0)
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=30000;")
        return db

    async def init_db(self):
        async with await self._connect() as db:
            await db.executescript(DatabaseModels.create_tables_sql())
            
            # Insere configuracao inicial de risco se tabela estiver vazia
            async with db.execute("SELECT COUNT(*) FROM risk_configs") as cursor:
                count = (await cursor.fetchone())[0]
                if count == 0:
                    await db.execute("""
                        INSERT INTO risk_configs (initial_stake, stop_loss_daily, take_profit_daily, max_daily_trades, max_single_stake, max_consecutive_losses, cooldown_minutes)
                        VALUES (1.0, 50.0, 100.0, 50, 20.0, 3, 15)
                    """)
            await db.commit()
        logger.info(f"Banco de dados SQLite '{self.db_path}' pronto.")

    async def get_risk_config(self) -> dict:
        async with await self._connect() as db:
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
        async with await self._connect() as db:
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
        async with await self._connect() as db:
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
        async with await self._connect() as db:
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
        async with await self._connect() as db:
            await db.execute(
                "INSERT INTO sessions (id) VALUES (?)",
                (session_id,)
            )
            await db.commit()

    async def save_trade(self, trade_data: dict):
        async with await self._connect() as db:
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

    async def get_daily_stats(self) -> dict:
        """Calcula PnL e quantidade de trades do dia atual."""
        async with await self._connect() as db:
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
