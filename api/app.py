import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.auth import require_dashboard_key, validate_websocket_key
from api.live_store import LiveStore
from api.routes.bots import router as bots_router
from api.routes.bot_control import router as legacy_bot_router
from api.routes.config import router as legacy_config_router
from api.routes.internal import router as internal_router
from api.routes.trades import router as legacy_trades_router
from api.websocket_manager import ws_manager
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    application.include_router(bots_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_bot_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_config_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(legacy_trades_router, dependencies=[Depends(require_dashboard_key)])
    application.include_router(internal_router)

    @application.get("/api/v1/health", include_in_schema=False)
    async def health():
        await repository.list_bots()
        return {"status": "ok", "database": "ok"}

    @application.get("/api/v1/strategies", dependencies=[Depends(require_dashboard_key)])
    async def strategies():
        return {"status": "success", "data": [{
            "id": "donchian",
            "name": "Donchian + ZigZag",
            "description": "Estrategia baseada no Donchian Channel com filtro de ZigZag puro",
        }]}

    @application.get("/api/v1/accounts", dependencies=[Depends(require_dashboard_key)])
    async def accounts():
        return {
            "status": "success",
            "data": [normalize_account(account) for account in await account_provider()],
        }

    @application.websocket("/api/v1/ws/bots/{bot_id}")
    async def live_bot(websocket: WebSocket, bot_id: str, key: str = ""):
        if not validate_websocket_key(key):
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
