import unittest

from data.market_data import MarketDataHandler


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.subscriptions = {}

    async def send(self, request, timeout=30):
        self.sent.append(request)
        return {
            "msg_type": "history",
            "candles": [
                {"epoch": 120, "open": 10, "high": 12, "low": 9, "close": 11},
                {"epoch": 180, "open": 11, "high": 13, "low": 10, "close": 12},
            ],
        }

    async def subscribe(self, key, request, handler):
        self.subscriptions[key] = (request, handler)
        return "sub-1"

    async def unsubscribe(self, key):
        self.subscriptions.pop(key, None)


class FakePublisher:
    def __init__(self):
        self.events = []
        self.started = False

    async def start(self):
        self.started = True

    async def publish(self, event):
        self.events.append(event)
        return True


class MarketDataHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_publishes_candle_history_and_registers_live_ticks(self):
        connection = FakeConnection()
        publisher = FakePublisher()
        handler = MarketDataHandler(connection, bot_id="bot-a", publisher=publisher)

        await handler.start("R_100", timeframe_seconds=60)

        self.assertEqual(connection.sent[0], {
            "ticks_history": "R_100",
            "end": "latest",
            "count": 500,
            "style": "candles",
            "granularity": 60,
        })
        history = publisher.events[0]
        self.assertEqual(history["type"], "market.history")
        self.assertEqual(history["mode"], "candles")
        self.assertEqual(history["points"][0]["time"], 120)
        self.assertIn("ticks:bot-a:R_100", connection.subscriptions)

    async def test_live_tick_publishes_price_candle_and_bot_identity(self):
        connection = FakeConnection()
        publisher = FakePublisher()
        handler = MarketDataHandler(connection, bot_id="bot-a", publisher=publisher)
        await handler.start("R_100", timeframe_seconds=60)
        _, callback = connection.subscriptions["ticks:bot-a:R_100"]

        await callback({"tick": {"epoch": 181, "quote": 12.5, "symbol": "R_100"}})

        tick_event = publisher.events[-1]
        self.assertEqual(tick_event["type"], "market.tick")
        self.assertEqual(tick_event["bot_id"], "bot-a")
        self.assertEqual(tick_event["price"], 12.5)
        self.assertEqual(tick_event["candle"]["time"], 180)


if __name__ == "__main__":
    unittest.main()
