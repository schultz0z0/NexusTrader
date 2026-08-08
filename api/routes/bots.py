from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from config.settings import settings
from strategies.nexus_speed import ALLOWED_ADX_THRESHOLDS

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
        submitted_strategy_config = dict(self.strategy_config)
        requested_adx_threshold = self.strategy_config.get("adx_threshold", 30)
        if self.strategy_id == "nexus_speed" and (
            type(requested_adx_threshold) is not int
            or requested_adx_threshold not in ALLOWED_ADX_THRESHOLDS
        ):
            raise ValueError("Nexus Speed aceita ADX mínimo 20, 25 ou 30")
        profiles = {
            "donchian": {
                "period": 21,
                "deviation": 1,
                "depth": 15,
                "backstep": 3,
            },
            "nexus_speed": {
                "ema_period": 5,
                "adx_period": 10,
                "adx_threshold": requested_adx_threshold,
                "atr_period": 14,
                "min_distance_atr": 0.30,
                "touch_tolerance_bps": 0,
                "ema_flat_tolerance_pips": 1,
                "min_profit_ratio": 0.87,
                "max_entry_delay_ticks": 1,
                "min_closed_candles": 270,
                "touch_window_start_second": 1,
                "touch_window_end_second": 30,
                "blocked_m5_candle_positions": [1, 5],
            },
        }
        if self.strategy_id not in profiles:
            raise ValueError("Estrategia nao suportada")
        fixed_strategy = profiles[self.strategy_id]
        if self.strategy_id == "nexus_speed":
            time_filter_keys = {
                "touch_window_start_second",
                "touch_window_end_second",
                "blocked_m5_candle_positions",
            }
            current_without_adx = {
                key: value
                for key, value in fixed_strategy.items()
                if key != "adx_threshold"
            }
            legacy_fixed_strategy = {
                key: value
                for key, value in fixed_strategy.items()
                if key not in time_filter_keys
            }
            legacy_fixed_without_adx = {
                key: value
                for key, value in legacy_fixed_strategy.items()
                if key != "adx_threshold"
            }
            legacy_banded_strategy = {
                **legacy_fixed_strategy,
                "touch_tolerance_bps": 1,
            }
            legacy_banded_without_adx = {
                key: value
                for key, value in legacy_banded_strategy.items()
                if key != "adx_threshold"
            }
            allowed_strategy_configs = (
                {},
                {"adx_threshold": requested_adx_threshold},
                current_without_adx,
                fixed_strategy,
                legacy_fixed_without_adx,
                legacy_fixed_strategy,
                legacy_banded_without_adx,
                legacy_banded_strategy,
            )
        else:
            allowed_strategy_configs = ({}, fixed_strategy)
        if submitted_strategy_config not in allowed_strategy_configs:
            raise ValueError("Parametros da estrategia sao fixos")
        self.strategy_config = fixed_strategy
        if self.timeframe_seconds != 60:
            raise ValueError("As estrategias operam somente em candles de 1 minuto")
        expected_duration = (2, "m") if self.strategy_id == "donchian" else (5, "t")
        if (self.duration, self.duration_unit) != expected_duration:
            description = "2 minutos" if self.strategy_id == "donchian" else "5 ticks"
            raise ValueError(f"{self.strategy_id} usa expiracao fixa de {description}")
        self.account_type = self.account_type.lower()
        if self.account_type not in {"demo", "real"}:
            raise ValueError("Tipo de conta deve ser demo ou real")
        if self.money_management not in {"fixed", "martingale", "soros"}:
            raise ValueError("Gestao de stake invalida")
        return self


class StartPayload(BaseModel):
    real_ticket: str = ""


class RealConfirmationPayload(BaseModel):
    phrase: str


def _repo(request):
    return request.app.state.repository


def _for_response(bot):
    if bot is None or bot.get("strategy_id") != "nexus_speed":
        return bot
    normalized = dict(bot)
    normalized["strategy_config"] = {
        **dict(bot.get("strategy_config") or {}),
        "touch_tolerance_bps": 0,
        "touch_window_start_second": 1,
        "touch_window_end_second": 30,
        "blocked_m5_candle_positions": [1, 5],
    }
    return normalized


@router.get("")
async def list_bots(request: Request):
    bots = await _repo(request).list_bots()
    return {"status": "success", "data": [_for_response(bot) for bot in bots]}


@router.post("", status_code=201)
async def create_bot(payload: BotPayload, request: Request):
    created = await _repo(request).create_bot(payload.model_dump())
    return {"status": "success", "data": _for_response(created)}


@router.post("/stop-all")
async def stop_all_bots(request: Request):
    stopped = await _repo(request).stop_all_bots()
    request.app.state.real_start_tickets.revoke_all()
    return {"status": "success", "data": {"stopped": stopped}}


@router.get("/{bot_id}")
async def get_bot(bot_id: str, request: Request):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    return {"status": "success", "data": _for_response(bot)}


@router.put("/{bot_id}")
async def update_bot(bot_id: str, payload: BotPayload, request: Request):
    if not await _repo(request).get_bot(bot_id):
        raise HTTPException(404, "Robo nao encontrado")
    updated = await _repo(request).update_bot(bot_id, payload.model_dump())
    return {"status": "success", "data": _for_response(updated)}


@router.delete("/{bot_id}", status_code=204)
async def delete_bot(bot_id: str, request: Request):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    if bot.get("desired_state") == "RUNNING":
        raise HTTPException(409, "Pare o robo antes de excluir")
    await _repo(request).delete_bot(bot_id)


@router.post("/{bot_id}/real-confirmation")
async def confirm_real_bot(
    bot_id: str,
    payload: RealConfirmationPayload,
    request: Request,
):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    if bot.get("account_type", "demo").lower() != "real":
        raise HTTPException(409, "Confirmacao REAL nao se aplica a conta demo")
    if not settings.ALLOW_REAL_TRADING:
        raise HTTPException(403, "Execucao real desabilitada no servidor")
    if settings.REAL_MAX_STAKE_USD <= 0:
        raise HTTPException(503, "Teto de stake REAL nao configurado")
    if float(bot.get("initial_stake") or 0) > settings.REAL_MAX_STAKE_USD:
        raise HTTPException(422, "Stake inicial excede o teto REAL do servidor")
    expected = f"REAL {bot['account_id']}"
    if payload.phrase.strip() != expected:
        raise HTTPException(422, f"Digite exatamente: {expected}")
    ticket = request.app.state.real_start_tickets.issue(bot)
    return {"status": "success", "data": {"ticket": ticket, "expires_in": 60}}


@router.post("/{bot_id}/start")
async def start_bot(bot_id: str, request: Request, payload: StartPayload = None):
    bot = await _repo(request).get_bot(bot_id)
    if not bot:
        raise HTTPException(404, "Robo nao encontrado")
    if bot.get("account_type", "demo").lower() == "real":
        if not settings.ALLOW_REAL_TRADING:
            raise HTTPException(403, "Execucao real desabilitada no servidor")
        if (
            settings.REAL_MAX_STAKE_USD <= 0
            or float(bot.get("initial_stake") or 0) > settings.REAL_MAX_STAKE_USD
        ):
            raise HTTPException(422, "Stake REAL excede o teto do servidor")
        if not request.app.state.real_start_tickets.consume(
            payload.real_ticket if payload else "", bot
        ):
            raise HTTPException(403, "Confirmacao REAL ausente, expirada ou invalida")
    if not bot.get("account_id"):
        raise HTTPException(422, "Configure uma conta Deriv antes de iniciar")
    updated = await _repo(request).set_desired_state(bot_id, "RUNNING")
    return {"status": "success", "data": _for_response(updated)}


@router.post("/{bot_id}/stop")
async def stop_bot(bot_id: str, request: Request):
    if not await _repo(request).get_bot(bot_id):
        raise HTTPException(404, "Robo nao encontrado")
    updated = await _repo(request).set_desired_state(bot_id, "STOPPED")
    return {"status": "success", "data": _for_response(updated)}


@router.get("/{bot_id}/trades")
async def bot_trades(bot_id: str, request: Request, limit: int = 100):
    return {"status": "success", "data": await _repo(request).list_trades(bot_id, limit)}


@router.get("/{bot_id}/order-intents")
async def bot_order_intents(bot_id: str, request: Request, limit: int = 100):
    if not await _repo(request).get_bot(bot_id):
        raise HTTPException(404, "Robo nao encontrado")
    return {
        "status": "success",
        "data": await _repo(request).list_order_intents(bot_id, limit),
    }


@router.get("/{bot_id}/snapshot")
async def bot_snapshot(bot_id: str, request: Request):
    return {"status": "success", "data": request.app.state.live_store.snapshot(bot_id)}

