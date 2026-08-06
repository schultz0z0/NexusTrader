"""Compatibility facade for v2 clients; state always comes from the database."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.auth import AuthManager

router = APIRouter(prefix="/api/v1/bot", tags=["Legacy compatibility"])

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


class BotSettingsSchema(BaseModel):
    account_id: str
    account_type: str = "demo"
    symbol: str = "R_100"


async def _default(request):
    bot = await request.app.state.repository.get_default_bot()
    if not bot:
        raise HTTPException(404, "Robo padrao nao encontrado")
    return bot


@router.get("/status")
async def get_bot_status(request: Request):
    return {"status": "success", "data": await _default(request)}


@router.get("/settings")
async def get_bot_settings(request: Request):
    return {"status": "success", "data": await _default(request)}


@router.post("/settings")
async def update_bot_settings(payload: BotSettingsSchema, request: Request):
    if payload.account_type.lower() != "demo":
        raise HTTPException(422, "Somente contas demo estao habilitadas")
    bot = await _default(request)
    updated = await request.app.state.repository.update_bot(bot["id"], payload.model_dump())
    return {"status": "success", "data": updated}


@router.post("/start")
async def start_bot(request: Request):
    bot = await _default(request)
    if not bot.get("account_id"):
        raise HTTPException(422, "Configure a conta demo antes de iniciar")
    updated = await request.app.state.repository.set_desired_state(bot["id"], "RUNNING")
    return {"status": "success", "data": updated}


@router.post("/stop")
async def stop_bot(request: Request):
    bot = await _default(request)
    updated = await request.app.state.repository.set_desired_state(bot["id"], "STOPPED")
    return {"status": "success", "data": updated}


@router.get("/accounts")
async def list_accounts():
    auth = AuthManager()
    try:
        return {"status": "success", "data": await auth.list_accounts()}
    finally:
        await auth.close()


@router.get("/assets")
async def list_assets():
    return {"status": "success", "data": SUPPORTED_ASSETS}
