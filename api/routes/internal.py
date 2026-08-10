from fastapi import APIRouter, Depends, Request

from api.auth import require_internal_token
from api.websocket_manager import ws_manager

router = APIRouter(prefix="/api/v1/internal", tags=["Internal"], dependencies=[Depends(require_internal_token)])


@router.post("/events", status_code=202)
async def ingest_event(event: dict, request: Request):
    if not event.get("type") or not event.get("bot_id"):
        return {"accepted": False, "error": "evento invalido"}
    event = request.app.state.live_store.sanitize_event(event)
    accepted = request.app.state.live_store.apply(event)
    if accepted:
        await ws_manager.broadcast(event["bot_id"], event)
    return {"accepted": True, "duplicate": not accepted}

