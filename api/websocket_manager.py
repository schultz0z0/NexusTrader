import json
import asyncio
from collections import defaultdict

from utils.logger import setup_logger

logger = setup_logger("WebSocketManager")


class LiveWebSocketManager:
    def __init__(self, send_timeout=2.0):
        self._connections = defaultdict(set)
        self.send_timeout = float(send_timeout)

    async def connect(self, bot_id, websocket, snapshot):
        await websocket.accept()
        self._connections[bot_id].add(websocket)
        await websocket.send_json({"type": "snapshot", "bot_id": bot_id, "data": snapshot})

    def disconnect(self, bot_id, websocket):
        self._connections[bot_id].discard(websocket)
        if not self._connections[bot_id]:
            self._connections.pop(bot_id, None)

    async def broadcast(self, bot_id, event):
        async def send(websocket):
            try:
                await asyncio.wait_for(
                    websocket.send_text(json.dumps(event)),
                    timeout=self.send_timeout,
                )
                return None
            except Exception:
                return websocket

        connections = tuple(self._connections.get(bot_id, ()))
        if not connections:
            return
        stale = await asyncio.gather(*(send(websocket) for websocket in connections))
        for websocket in filter(None, stale):
            self.disconnect(bot_id, websocket)


ws_manager = LiveWebSocketManager()

