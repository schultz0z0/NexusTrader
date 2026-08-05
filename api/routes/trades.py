from fastapi import APIRouter
from database.repository import DatabaseRepository
import aiosqlite

router = APIRouter(prefix="/api/v1/trades", tags=["Trades"])
db = DatabaseRepository()

@router.get("/stats")
async def get_daily_stats():
    stats = await db.get_daily_stats()
    return {"status": "success", "data": stats}

@router.get("/list")
async def list_recent_trades(limit: int = 20):
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("""
            SELECT id, session_id, strategy_name, symbol, contract_type, contract_id, stake, payout, profit, result, created_at
            FROM trades
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return {"status": "success", "data": [dict(r) for r in rows]}
