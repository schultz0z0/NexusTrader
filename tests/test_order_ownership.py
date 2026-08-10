import tempfile
import unittest
from pathlib import Path

from database.repository import DatabaseRepository
from trading.executor import AmbiguousBuyError, OrderExecutor
from trading.ownership import (
    ActiveOrderIntentError,
    BuyTransactionTracker,
    OrderOwnershipCoordinator,
    OrderOwnershipReconciler,
)


async def provision_order_test_bots(repository):
    for bot_id in ("bot-a", "bot-b"):
        await repository.create_bot({
            "id": bot_id,
            "name": f"Order ownership {bot_id}",
            "strategy_id": "donchian",
            "account_id": "DOT100",
            "account_type": "demo",
            "symbol": "R_75",
            "timeframe_seconds": 60,
            "duration": 2,
            "duration_unit": "m",
            "initial_stake": 1.0,
            "money_management": "fixed",
        })


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    async def send(self, request):
        self.sent.append(dict(request))
        return self.responses.pop(0)


class OrderIntentRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(str(Path(self.tempdir.name) / "orders.db"))
        await self.repository.init_db()
        await provision_order_test_bots(self.repository)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def payload(bot_id="bot-a"):
        return {
            "bot_id": bot_id,
            "account_id": "DOT100",
            "session_id": "session-a",
            "proposal_id": "proposal-a",
            "symbol": "R_75",
            "contract_type": "CALL",
            "stake": 1.0,
            "price": 1.0,
            "duration": 2,
            "duration_unit": "m",
            "signal_epoch": 1000,
        }

    async def test_only_one_unresolved_buy_window_exists_per_account(self):
        first = await self.repository.create_order_intent(self.payload())

        with self.assertRaises(ActiveOrderIntentError):
            await self.repository.create_order_intent(self.payload(bot_id="bot-b"))

        self.assertEqual(first["state"], "prepared")

    async def test_owned_intent_releases_account_purchase_window(self):
        first = await self.repository.create_order_intent(self.payload())
        await self.repository.mark_order_intent_owned(first["id"], {
            "contract_id": 42,
            "transaction_id": 99,
        })

        second = await self.repository.create_order_intent(self.payload(bot_id="bot-b"))

        self.assertEqual(second["state"], "prepared")

    async def test_owned_intent_without_trade_remains_visible_for_crash_recovery(self):
        intent = await self.repository.create_order_intent(self.payload())
        await self.repository.mark_order_intent_owned(intent["id"], {
            "contract_id": 42, "contract_type": "CALL", "buy_price": 1.0,
        })

        orphans = await self.repository.list_owned_intents_without_trade("bot-a")

        self.assertEqual([item["contract_id"] for item in orphans], [42])


class OrderExecutionOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_timeout_is_ambiguous_and_is_not_reported_as_rejection(self):
        connection = FakeConnection([{
            "error": {"code": "Timeout", "message": "Request timed out"},
        }])
        executor = OrderExecutor(connection, account_type="demo")

        with self.assertRaises(AmbiguousBuyError):
            await executor.buy("proposal-a", 1.0, passthrough={"order_intent_id": "intent-a"})

        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(connection.sent[0]["passthrough"]["order_intent_id"], "intent-a")

    async def test_transaction_stream_captures_buy_for_lost_response_reconciliation(self):
        class StreamingConnection:
            async def subscribe(self, key, request, handler):
                self.key = key
                self.request = request
                self.handler = handler

        connection = StreamingConnection()
        tracker = BuyTransactionTracker(connection)
        await tracker.start()
        await connection.handler({"transaction": {
            "action": "buy", "contract_id": 42, "transaction_time": 1002,
            "amount": -1.0, "symbol": "R_75",
        }})

        self.assertEqual(connection.request, {"transaction": 1})
        self.assertEqual(tracker.snapshot()[0]["contract_id"], 42)


class OrderReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = DatabaseRepository(str(Path(self.tempdir.name) / "reconcile.db"))
        await self.repository.init_db()
        await provision_order_test_bots(self.repository)
        self.intent = await self.repository.create_order_intent(
            OrderIntentRepositoryTests.payload()
        )
        await self.repository.update_order_intent(
            self.intent["id"], "reconcile_pending", error="timeout"
        )

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_unique_statement_candidate_restores_contract_ownership(self):
        connection = FakeConnection([
            {"portfolio": {"contracts": []}},
            {"statement": {"transactions": [{
                "action_type": "buy",
                "contract_id": 42,
                "transaction_id": 99,
                "transaction_time": 1002,
                "amount": -1.0,
                "symbol": "R_75",
                "contract_type": "CALL",
                "buy_price": 1.0,
                "date_expiry": 1122,
            }]}},
        ])
        reconciler = OrderOwnershipReconciler(connection, self.repository)

        contract = await reconciler.reconcile(await self.repository.get_order_intent(self.intent["id"]))

        self.assertEqual(contract["contract_id"], 42)
        stored = await self.repository.get_order_intent(self.intent["id"])
        self.assertEqual(stored["state"], "owned")
        self.assertEqual(stored["contract_id"], 42)

    async def test_prepared_but_never_submitted_intent_is_safely_cancelled(self):
        await self.repository.update_order_intent(self.intent["id"], "cancelled")
        prepared = await self.repository.create_order_intent(
            OrderIntentRepositoryTests.payload()
        )
        coordinator = OrderOwnershipCoordinator(
            FakeConnection([]), self.repository, account_type="demo"
        )

        resolved = await coordinator.reconcile_pending("bot-a")

        self.assertEqual(resolved, [])
        stored = await self.repository.get_order_intent(prepared["id"])
        self.assertEqual(stored["state"], "cancelled")

    async def test_multiple_matching_candidates_are_quarantined_not_guessed(self):
        candidates = [{
            "action_type": "buy", "contract_id": contract_id,
            "transaction_time": 1002, "amount": -1.0,
            "symbol": "R_75", "contract_type": "CALL",
        } for contract_id in (42, 43)]
        connection = FakeConnection([
            {"portfolio": {"contracts": []}},
            {"statement": {"transactions": candidates}},
        ])
        reconciler = OrderOwnershipReconciler(connection, self.repository)

        contract = await reconciler.reconcile(await self.repository.get_order_intent(self.intent["id"]))

        self.assertIsNone(contract)
        stored = await self.repository.get_order_intent(self.intent["id"])
        self.assertEqual(stored["state"], "ambiguous")

    async def test_coordinator_persists_before_buy_and_never_blind_retries(self):
        await self.repository.update_order_intent(self.intent["id"], "cancelled")
        connection = FakeConnection([
            {"error": {"code": "Timeout", "message": "lost response"}},
            {"portfolio": {"contracts": []}},
            {"statement": {"transactions": []}},
        ])
        coordinator = OrderOwnershipCoordinator(
            connection,
            self.repository,
            account_type="demo",
        )

        result = await coordinator.buy({
            **OrderIntentRepositoryTests.payload(),
            "proposal_id": "proposal-new",
        })

        self.assertIsNone(result)
        buy_requests = [item for item in connection.sent if "buy" in item]
        self.assertEqual(len(buy_requests), 1)
        pending = await self.repository.list_unresolved_order_intents("bot-a")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["state"], "reconcile_pending")


if __name__ == "__main__":
    unittest.main()
