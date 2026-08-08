import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.auth import require_dashboard_key
from api.live_store import LiveStore
from api.real_tickets import RealStartTicketStore
from api.routes.bots import router as bots_router
from api.routes.bot_control import router as legacy_bot_router
from api.routes.config import router as legacy_config_router
from api.routes.internal import router as internal_router
from api.routes.trades import router as legacy_trades_router
from api.websocket_manager import ws_manager
from api.ws_tickets import WebSocketTicketStore
from core.accounts import normalize_account
from core.auth import AuthManager
from database.repository import DatabaseRepository
from utils.logger import setup_logger

logger = setup_logger("FastAPIApp")
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))


async def _deriv_account_provider():
    auth = AuthManager()
    try:
        return await auth.list_accounts()
    finally:
        await auth.close()


def create_app(repository=None, live_store=None, account_provider=None):
    repository = repository or DatabaseRepository()
    live_store = live_store or LiveStore()
    account_provider = account_provider or _deriv_account_provider
    ticket_store = WebSocketTicketStore()
    real_ticket_store = RealStartTicketStore()

    @asynccontextmanager
    async def lifespan(application):
        await repository.init_db()
        yield

    application = FastAPI(
        title="NexusTrader Control Plane",
        description="Controle multi-robo e telemetria Deriv",
        version="3.0.0",
        lifespan=lifespan,
    )
    application.state.repository = repository
    application.state.live_store = live_store
    application.state.real_start_tickets = real_ticket_store
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; connect-src 'self' ws: wss:; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response
    application.include_router(bots_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_bot_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_config_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_trades_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(internal_router, include_in_schema=False)

    @application.get("/api/v1/health/live", include_in_schema=False)
    async def health_live():
        return {"status": "alive"}

    async def readiness_response():
        try:
            result = await repository.readiness()
        except Exception as exc:
            logger.exception("Readiness falhou: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "checks": {"database": "error"}},
            )
        return JSONResponse(
            status_code=200 if result["ready"] else 503,
            content={
                "status": "ready" if result["ready"] else "not_ready",
                "checks": result["checks"],
            },
        )

    @application.get("/api/v1/health/ready", include_in_schema=False)
    async def health_ready():
        return await readiness_response()

    @application.get("/api/v1/health", include_in_schema=False)
    async def health():
        return await readiness_response()

    @application.get("/api/v1/strategies", dependencies=[Depends(require_dashboard_key)])
    async def strategies():
        return {"status": "success", "data": [
            {
                "id": "donchian",
                "name": "Donchian + ZigZag",
                "description": "Donchian Channel com filtro de ZigZag puro",
            },
            {
                "id": "nexus_speed",
                "name": "Nexus Speed",
                "description": "Pullback na EMA(5), ADX(10), ATR(14) e expiracao de 5 ticks",
            },
        ]}

    @application.get("/api/v1/accounts", dependencies=[Depends(require_dashboard_key)])
    async def accounts():
        return {
            "status": "success",
            "data": [normalize_account(account) for account in await account_provider()],
        }

    @application.post("/api/v1/ws-tickets/{bot_id}", dependencies=[Depends(require_dashboard_key)])
    async def websocket_ticket(bot_id: str):
        return {"status": "success", "data": {"ticket": ticket_store.issue(bot_id)}}

    @application.websocket("/api/v1/ws/bots/{bot_id}")
    async def live_bot(websocket: WebSocket, bot_id: str, ticket: str = ""):
        if not ticket_store.consume(ticket, bot_id):
            await websocket.close(code=4401)
            return
        await ws_manager.connect(bot_id, websocket, live_store.snapshot(bot_id))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(bot_id, websocket)
        except Exception:
            ws_manager.disconnect(bot_id, websocket)

    if os.path.isdir(STATIC_DIR):
        application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/")
    async def dashboard():
        index = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return {"message": "NexusTrader Control Plane"}

    return application


app = create_app()
