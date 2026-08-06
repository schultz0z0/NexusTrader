from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from config.settings import settings

router = APIRouter(prefix="/api/v1/bots", tags=["Bots"])


class BotPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    strategy_id: str = "donchian"
    strategy_config: dict = Field(default_factory=dict)
    account_id: str = ""
    account_type: str = "demo"
    symbol: str = "R_75"
    timeframe_seconds: int = Field(default=60, ge=1)
    duration: int = Field(default=2, ge=1)
    duration_unit: str = "m"
    initial_stake: float = Field(default=1.0, gt=0)
    money_management: str = "fixed"
    money_config: dict = Field(default_factory=dict)
    risk_config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def demo_only(self):
        if self.strategy_id != "donchian":
            raise ValueError("Somente a estrategia Donchian + ZigZag esta habilitada")
        if self.account_type.lower() != "demo" and not settings.ALLOW_REAL_TRADING:
            raise ValueError("Somente contas demo estao habilitadas")
        if self.money_management not in {"fixed", "martingale", "soros"}:
            raise ValueError("Gestao de stake invalida")
        return self


def _repo(request):
    return request.app.state.repository


@router.get("")
async def list_bots(request: Request):
    return {"status": "success", "data": await _repo(request).list_bots()}


@router.post("", status_code=201)
async def create_bot(payload: BotPayload, request: Request):
    return {"status": "success", "data": await _repo(request).create_bot(payload.model_dump())}


@router.get("/{bot_id}")
async def get_bot(bot_id: str, request: Request):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    return {"status": "success", "data": bot}


@router.put("/{bot_id}")
async def update_bot(bot_id: str, payload: BotPayload, request: Request):
    if not await _repo(request).get_bot(bot_id):
        raise HTTPException(404, "Robo nao encontrado")
    return {"status": "success", "data": await _repo(request).update_bot(bot_id, payload.model_dump())}


@router.delete("/{bot_id}", status_code=204)
async def delete_bot(bot_id: str, request: Request):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    if bot.get("desired_state") == "RUNNING":
        raise HTTPException(409, "Pare o robo antes de excluir")
    await _repo(request).delete_bot(bot_id)


@router.post("/{bot_id}/start")
async def start_bot(bot_id: str, request: Request):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    if bot.get("account_type", "demo").lower() != "demo" and not settings.ALLOW_REAL_TRADING:
        raise HTTPException(422, "Somente contas demo estao habilitadas")
    if not bot.get("account_id"):
        raise HTTPException(422, "Configure a conta demo antes de iniciar")
    return {"status": "success", "data": await _repo(request).set_desired_state(bot_id, "RUNNING")}


@router.post("/{bot_id}/stop")
async def stop_bot(bot_id: str, request: Request):
    if not await _repo(request).get_bot(bot_id):
        raise HTTPException(404, "Robo nao encontrado")
    return {"status": "success", "data": await _repo(request).set_desired_state(bot_id, "STOPPED")}


@router.get("/{bot_id}/trades")
async def bot_trades(bot_id: str, request: Request, limit: int = 100):
    return {"status": "success", "data": await _repo(request).list_trades(bot_id, limit)}


@router.get("/{bot_id}/snapshot")
async def bot_snapshot(bot_id: str, request: Request):
    return {"status": "success", "data": request.app.state.live_store.snapshot(bot_id)}

