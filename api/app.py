from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routes.config import router as config_router
from api.routes.trades import router as trades_router
from api.routes.bot_control import router as bot_control_router
from api.routes.notifications import router as notifications_router
from api.websocket_manager import ws_manager
from utils.logger import setup_logger
import os

logger = setup_logger("FastAPIApp")

app = FastAPI(
    title="NexusTrader API",
    description="API REST de Controle e Monitoramento para o NexusTrader (Deriv Platform)",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router)
app.include_router(trades_router)
app.include_router(bot_control_router)
app.include_router(notifications_router)

@app.websocket("/api/v1/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Mantem conexao viva escutando pings do client
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        ws_manager.disconnect(websocket)

@app.on_event("startup")
async def startup_event():
    from database.repository import DatabaseRepository
    db = DatabaseRepository()
    await db.init_db()
    logger.info("FastAPI: Banco de dados inicializado no startup da API.")

static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def serve_dashboard():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "NexusTrader API v2.0 - Dashboard frontend nao encontrado em /static"}
