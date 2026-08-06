import asyncio
import json
import unittest

from core.connection import NexusConnection


_CLOSED = object()


class FakeAuth:
    def __init__(self):
        self.requested_accounts = []
        self.closed = False

    async def get_websocket_url(self, account_id=None):
        self.requested_accounts.append(account_id)
        return f"wss://example.test/{account_id}/{len(self.requested_accounts)}"

    async def close(self):
        self.closed = True


class FakeWebSocket:
    def __init__(self, name):
        self.name = name
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send(self, raw_message):
        request = json.loads(raw_message)
        self.sent.append(request)
        if "ticks" in request:
            await self.incoming.put(json.dumps({
                "msg_type": "tick",
                "req_id": request["req_id"],
                "tick": {"epoch": 1, "quote": 100.0, "symbol": request["ticks"]},
                "subscription": {"id": f"sub-{self.name}"},
            }))
        elif "ping" in request:
            await self.incoming.put(json.dumps({"msg_type": "ping", "req_id": request["req_id"], "ping": "pong"}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is _CLOSED:
            raise ConnectionError("server disconnected")
        return item

    async def force_disconnect(self):
        await self.incoming.put(_CLOSED)

    async def close(self):
        self.closed = True
        await self.incoming.put(_CLOSED)


class ConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auth = FakeAuth()
        self.sockets = []

        async def connector(url, **kwargs):
            socket = FakeWebSocket(str(len(self.sockets) + 1))
            self.sockets.append(socket)
            return socket

        self.connection = NexusConnection(
            self.auth,
            connector=connector,
            reconnect_delays=(0,),
            heartbeat_interval=3600,
        )

    async def asyncTearDown(self):
        await self.connection.disconnect()

    async def test_connect_requests_one_account_scoped_otp(self):
        connected = await self.connection.connect("DOT-DEMO")

        self.assertTrue(connected)
        self.assertEqual(self.auth.requested_accounts, ["DOT-DEMO"])
        self.assertIn("DOT-DEMO", self.connection.websocket_url)

    async def test_reconnect_replays_registered_tick_subscription(self):
        received = []

        async def handler(message):
            received.append(message["tick"]["quote"])

        await self.connection.connect("DOT-DEMO")
        await self.connection.subscribe("ticks:R_100", {"ticks": "R_100"}, handler)
        await self.sockets[0].force_disconnect()
        await self.connection.wait_until_connected(timeout=1, minimum_generation=2)

        first_requests = [request for request in self.sockets[0].sent if request.get("ticks") == "R_100"]
        second_requests = [request for request in self.sockets[1].sent if request.get("ticks") == "R_100"]
        self.assertEqual(len(first_requests), 1)
        self.assertEqual(len(second_requests), 1)
        self.assertEqual(self.auth.requested_accounts, ["DOT-DEMO", "DOT-DEMO"])
        self.assertGreaterEqual(len(received), 2)


if __name__ == "__main__":
    unittest.main()
