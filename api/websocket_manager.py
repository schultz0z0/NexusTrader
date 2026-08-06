import json
from collections import defaultdict

from utils.logger import setup_logger

logger = setup_logger("WebSocketManager")


class LiveWebSocketManager:
    def __init__(self):
        self._connections = defaultdict(set)

    async def connect(self, bot_id, websocket, snapshot):
        await websocket.accept()
        self._connections[bot_id].add(websocket)
        await websocket.send_json({"type": "snapshot", "bot_id": bot_id, "data": snapshot})

    def disconnect(self, bot_id, websocket):
        self._connections[bot_id].discard(websocket)
        if not self._connections[bot_id]:
            self._connections.pop(bot_id, None)

    async def broadcast(self, bot_id, event):
        stale = []
        for websocket in tuple(self._connections.get(bot_id, ())):
            try:
                await websocket.send_text(json.dumps(event))
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(bot_id, websocket)


ws_manager = LiveWebSocketManager()

