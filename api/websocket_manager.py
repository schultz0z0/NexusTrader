import asyncio
import json
from typing import List
from fastapi import WebSocket
from utils.logger import setup_logger

logger = setup_logger("WebSocketManager")

class LiveWebSocketManager:
    """
    Gerencia conexoes WebSocket ativas com o Dashboard Web
    e transmite ticks/indicadores em tempo real.
    """
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Dashboard Web conectado via WebSocket. Total conexoes: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("Dashboard Web desconectado.")

    async def broadcast(self, data: dict):
        if not self.active_connections:
            return
            
        message = json.dumps(data)
        to_remove = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                to_remove.append(connection)
                
        for conn in to_remove:
            self.disconnect(conn)

ws_manager = LiveWebSocketManager()
