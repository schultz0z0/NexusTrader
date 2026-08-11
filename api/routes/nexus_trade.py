from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.websocket_manager import ws_manager
from config.settings import settings
from core.events import runtime_event
from nexus_trade.constants import NEXUS_DEMO_STAKE, NEXUS_TRADE_BOT_ID


router = APIRouter(prefix="/api/v1/nexus-trade", tags=["NexusTrade"])


class ChampionModePayload(BaseModel):
    enabled: bool
    account_id: str
    account_type: Literal["demo", "real"]
    real_ticket: str = ""


class EmergencyStopPayload(BaseModel):
    enabled: bool = True


class NexusRealConfirmationPayload(BaseModel):
    account_id: str = Field(min_length=1)
    phrase: str


def _repo(request: Request):
    return request.app.state.repository


async def _snapshot(request: Request) -> dict:
    durable = await _repo(request).get_nexus_control_snapshot()
    return request.app.state.live_store.hydrate_nexus(durable)


async def _publish_runtime_snapshot(request: Request) -> dict:
    snapshot = await _snapshot(request)
    event = runtime_event(
        "nexus.runtime",
        NEXUS_TRADE_BOT_ID,
        snapshot_version=snapshot["snapshot_version"],
        payload={
            "runtime": snapshot["runtime"],
            "emergency_stop": snapshot["emergency_stop"],
        },
    )
    event = request.app.state.live_store.sanitize_event(event)
    if request.app.state.live_store.apply(event):
        await ws_manager.broadcast(NEXUS_TRADE_BOT_ID, event)
    return request.app.state.live_store.snapshot(NEXUS_TRADE_BOT_ID)


def _real_ticket_bot(bot: dict, account_id: str) -> dict:
    return {
        "id": NEXUS_TRADE_BOT_ID,
        "account_id": account_id,
        "config_revision": int(bot.get("config_revision", 1)),
    }


@router.get("")
async def nexus_snapshot(request: Request):
    return {"status": "success", "data": await _snapshot(request)}


@router.post("/mode")
@router.put("/mode", include_in_schema=False)
async def set_champion_mode(payload: ChampionModePayload, request: Request):
    bot = await _repo(request).get_bot(NEXUS_TRADE_BOT_ID)
    if bot is None:
        raise HTTPException(503, "NexusTrade nao esta provisionado")
    if payload.enabled and payload.account_type == "real":
        if not settings.ALLOW_REAL_TRADING:
            raise HTTPException(403, "Execucao real desabilitada no servidor")
        if (
            settings.REAL_MAX_STAKE_USD <= 0
            or NEXUS_DEMO_STAKE > settings.REAL_MAX_STAKE_USD
        ):
            raise HTTPException(422, "Stake REAL excede o teto do servidor")
        if not request.app.state.real_start_tickets.consume(
            payload.real_ticket,
            _real_ticket_bot(bot, payload.account_id),
        ):
            raise HTTPException(403, "Confirmacao REAL ausente, expirada ou invalida")
    try:
        await _repo(request).set_nexus_champion_mode(
            enabled=payload.enabled,
            account_id=payload.account_id,
            account_type=payload.account_type,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    request.app.state.real_start_tickets.revoke_all()
    return {"status": "success", "data": await _publish_runtime_snapshot(request)}


@router.post("/real-confirmation")
async def confirm_real_champion(
    payload: NexusRealConfirmationPayload,
    request: Request,
):
    if not settings.ALLOW_REAL_TRADING:
        raise HTTPException(403, "Execucao real desabilitada no servidor")
    if settings.REAL_MAX_STAKE_USD <= 0 or NEXUS_DEMO_STAKE > settings.REAL_MAX_STAKE_USD:
        raise HTTPException(422, "Stake REAL excede o teto do servidor")
    account_id = payload.account_id.strip()
    expected = f"REAL {account_id}"
    if payload.phrase.strip() != expected:
        raise HTTPException(422, f"Digite exatamente: {expected}")
    bot = await _repo(request).get_bot(NEXUS_TRADE_BOT_ID)
    if bot is None:
        raise HTTPException(503, "NexusTrade nao esta provisionado")
    ticket = request.app.state.real_start_tickets.issue(
        _real_ticket_bot(bot, account_id),
    )
    return {"status": "success", "data": {"ticket": ticket, "expires_in": 60}}


@router.post("/emergency-stop")
async def emergency_stop(payload: EmergencyStopPayload, request: Request):
    await _repo(request).set_nexus_emergency_stop(payload.enabled)
    request.app.state.real_start_tickets.revoke_all()
    return {"status": "success", "data": await _publish_runtime_snapshot(request)}


async def _list_response(request: Request, repository_method: str):
    data = await getattr(_repo(request), repository_method)()
    return {
        "status": "success",
        "data": request.app.state.live_store.sanitize_event(data),
    }


@router.get("/versions")
async def versions(request: Request):
    return await _list_response(request, "list_nexus_versions")


@router.get("/campaigns")
async def campaigns(request: Request):
    return await _list_response(request, "list_nexus_campaigns")


@router.get("/reports")
async def reports(request: Request):
    return await _list_response(request, "list_nexus_reports")


@router.get("/reports/weekly/{aligned_week}")
async def weekly_report(aligned_week: str, request: Request):
    try:
        report = await _repo(request).get_nexus_weekly_report(aligned_week)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if report is None:
        raise HTTPException(404, "Relatorio semanal nao encontrado")
    return {
        "status": "success",
        "data": request.app.state.live_store.sanitize_event(report),
    }


@router.get("/reports/{report_id}/exports/{format_name}")
async def report_export(report_id: str, format_name: str, request: Request):
    try:
        artifact = await _repo(request).get_nexus_export(report_id, format_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if artifact is None:
        raise HTTPException(404, "Relatorio nao encontrado")
    return Response(
        content=artifact["content"],
        media_type=artifact["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{artifact["filename"]}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reports/{report_id}")
async def report_detail(report_id: str, request: Request):
    report = await _repo(request).get_nexus_report(report_id)
    if report is None:
        raise HTTPException(404, "Relatorio nao encontrado")
    return {
        "status": "success",
        "data": request.app.state.live_store.sanitize_event(report),
    }


@router.get("/proposals")
async def proposals(request: Request):
    return await _list_response(request, "list_nexus_proposals")


@router.get("/exports")
async def exports(request: Request):
    return await _list_response(request, "list_nexus_exports")
