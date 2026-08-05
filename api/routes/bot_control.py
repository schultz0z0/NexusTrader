from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from database.repository import DatabaseRepository
from core.auth import AuthManager

router = APIRouter(prefix="/api/v1/bot", tags=["BotControl"])
db = DatabaseRepository()

# Estado global compartilhado (Sera substituido pelo BD em partes)
BOT_STATE = {
    "status": "RUNNING",  # RUNNING, PAUSED, STOPPED
}

SUPPORTED_ASSETS = [
    {"symbol": "R_10", "name": "Volatility 10 Index"},
    {"symbol": "R_25", "name": "Volatility 25 Index"},
    {"symbol": "R_50", "name": "Volatility 50 Index"},
    {"symbol": "R_75", "name": "Volatility 75 Index"},
    {"symbol": "R_100", "name": "Volatility 100 Index"},
    {"symbol": "1HZ10V", "name": "Volatility 10 (1s) Index"},
    {"symbol": "1HZ25V", "name": "Volatility 25 (1s) Index"},
    {"symbol": "1HZ50V", "name": "Volatility 50 (1s) Index"},
    {"symbol": "1HZ75V", "name": "Volatility 75 (1s) Index"},
    {"symbol": "1HZ100V", "name": "Volatility 100 (1s) Index"},
]

@router.get("/status")
async def get_bot_status():
    settings = await db.get_bot_settings()
    BOT_STATE.update(settings)
    return {"status": "success", "data": BOT_STATE}

@router.get("/settings")
async def get_bot_settings():
    settings = await db.get_bot_settings()
    return {"status": "success", "data": settings}

class BotSettingsSchema(BaseModel):
    account_id: str
    account_type: str
    symbol: str

@router.post("/settings")
async def update_bot_settings(payload: BotSettingsSchema):
    await db.update_bot_settings(payload.model_dump())
    return {"status": "success", "message": "Configurações do robô atualizadas. O motor será reiniciado para aplicar.", "data": payload.model_dump()}

@router.get("/accounts")
async def list_accounts():
    auth = AuthManager()
    accounts = await auth.list_accounts()
    await auth.close()
    return {"status": "success", "data": accounts}

@router.get("/assets")
async def list_assets():
    return {"status": "success", "data": SUPPORTED_ASSETS}

@router.post("/start")
async def start_bot():
    BOT_STATE["status"] = "RUNNING"
    return {"status": "success", "message": "NexusTrader INICIADO com sucesso!", "data": BOT_STATE}

@router.post("/stop")
async def stop_bot():
    BOT_STATE["status"] = "STOPPED"
    return {"status": "success", "message": "NexusTrader PAUSADO com sucesso!", "data": BOT_STATE}

from api.websocket_manager import ws_manager

class TickData(BaseModel):
    type: str
    symbol: str
    price: float
    epoch: int
    upper: Optional[float] = None
    lower: Optional[float] = None
    sma: Optional[float] = None

@router.post("/tick")
async def broadcast_tick(tick: TickData):
    await ws_manager.broadcast(tick.dict())
    return {"status": "success"}
