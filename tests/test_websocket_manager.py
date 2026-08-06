import asyncio
import unittest

from api.websocket_manager import LiveWebSocketManager


class FastWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, message):
        self.messages.append(message)


class SlowWebSocket:
    async def send_text(self, message):
        await asyncio.Event().wait()


class WebSocketManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_browser_does_not_block_fast_browser_and_is_disconnected(self):
        manager = LiveWebSocketManager(send_timeout=0.01)
        fast = FastWebSocket()
        slow = SlowWebSocket()
        manager._connections["bot-a"].update({slow, fast})

        await manager.broadcast("bot-a", {"type": "market.tick", "bot_id": "bot-a"})

        self.assertEqual(len(fast.messages), 1)
        self.assertNotIn(slow, manager._connections["bot-a"])


if __name__ == "__main__":
    unittest.main()
