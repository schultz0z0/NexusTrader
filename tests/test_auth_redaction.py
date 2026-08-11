import logging
import unittest

from core.auth import AuthManager, logger as auth_logger
from core.connection import NexusConnection, logger as connection_logger


SENTINELS = (
    "ROT-REAL-SENTINEL",
    "DOT-DEMO-SENTINEL",
    "98765.43",
    "TOKEN-SENTINEL",
    "account_list_raw",
)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class FakeResponse:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    async def get(self, _path):
        return FakeResponse(200, [
            {"account_id": "ROT-REAL-SENTINEL", "account_type": "real", "balance": "98765.43"},
            {"account_id": "DOT-DEMO-SENTINEL", "account_type": "demo", "balance": "1.00"},
        ])

    async def post(self, _path):
        return FakeResponse(
            500,
            {"account_list_raw": "TOKEN-SENTINEL"},
            text="TOKEN-SENTINEL DOT-DEMO-SENTINEL 98765.43 account_list_raw",
        )

    async def aclose(self):
        pass


class AuthBoundaryRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_logs_only_structural_account_and_http_outcomes(self):
        capture = Capture()
        auth_logger.addHandler(capture)
        manager = AuthManager()
        await manager._client.aclose()
        manager._client = FakeClient()
        try:
            accounts = await manager.list_accounts()
            websocket_url = await manager.get_websocket_url("DOT-DEMO-SENTINEL")
        finally:
            auth_logger.removeHandler(capture)
            await manager.close()

        self.assertEqual(len(accounts), 2)
        self.assertIsNone(websocket_url)
        serialized = "\n".join(capture.messages)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, serialized)
        self.assertNotIn("balance", serialized.lower())
        self.assertIn("accounts=2", serialized)
        self.assertIn("http_status=500", serialized)

    async def test_connection_logs_and_errors_never_include_selected_identity(self):
        class MissingOtpAuth:
            async def get_websocket_url(self, _account_id):
                return None

            async def close(self):
                pass

        capture = Capture()
        connection_logger.addHandler(capture)
        connection = NexusConnection(MissingOtpAuth())
        try:
            connected = await connection.connect("DOT-DEMO-SENTINEL")
        finally:
            connection_logger.removeHandler(capture)
            await connection.disconnect()

        self.assertFalse(connected)
        serialized = "\n".join(capture.messages)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, serialized)
        self.assertIn("error_type=ConnectionError", serialized)


if __name__ == "__main__":
    unittest.main()
