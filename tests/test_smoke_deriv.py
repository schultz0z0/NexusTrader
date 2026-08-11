import asyncio
import json
import logging
import unittest
from unittest.mock import patch

from scripts import smoke_deriv


class DerivSmokeContractTests(unittest.TestCase):
    def test_dot_login_is_recognized_as_demo(self):
        self.assertTrue(smoke_deriv.is_demo({"account_id": "VRTC12345678"}))
        self.assertFalse(smoke_deriv.is_demo({"account_id": "CR87654321"}))

    def test_smoke_cli_has_no_trade_mode(self):
        parser = smoke_deriv.build_parser()

        _, unknown = parser.parse_known_args(["--trade"])

        self.assertEqual(unknown, ["--trade"])

    def test_public_summary_proves_demo_without_printing_account_identity(self):
        """Catches account IDs leaking from the read-only Deriv smoke."""

        class FakeAuth:
            async def list_accounts(self):
                logging.getLogger("AuthManager").warning("DOT-SECRET balance response")
                return [{"account_id": "DOT-SECRET", "account_type": "demo"}]

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                pass

            async def connect(self, _account_id):
                return True

            async def send(self, _request):
                return {"history": {"prices": [1.0, 1.1]}}

            async def subscribe(self, _key, _request, callback):
                await callback({"tick": {"quote": 1.1}})

            async def unsubscribe(self, _key):
                pass

            async def disconnect(self):
                pass

        printed = []
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        capture = Capture()
        logging.getLogger("AuthManager").addHandler(capture)
        with (
            patch.object(smoke_deriv, "AuthManager", FakeAuth),
            patch.object(smoke_deriv, "NexusConnection", FakeConnection),
            patch("builtins.print", side_effect=printed.append),
        ):
            try:
                asyncio.run(smoke_deriv.run())
            finally:
                logging.getLogger("AuthManager").removeHandler(capture)

        payload = json.loads(printed[0])
        self.assertTrue(payload.get("demo_account_verified"))
        self.assertNotIn("demo_account", payload)
        self.assertNotIn("DOT-SECRET", printed[0])
        self.assertEqual(records, [])

    def test_main_fails_closed_without_exception_or_identity_output(self):
        """Catches traceback/account leakage when the external probe fails."""

        async def failed_run(_symbol="R_100"):
            raise RuntimeError("DOT-SECRET raw authorization payload")

        printed = []
        with (
            patch.object(smoke_deriv, "run", failed_run),
            patch("builtins.print", side_effect=printed.append),
        ):
            exit_code = smoke_deriv.main([])

        self.assertEqual(exit_code, 2)
        self.assertEqual(printed, ['{"outcome":"SKIPPED_SAFE"}'])


if __name__ == "__main__":
    unittest.main()
