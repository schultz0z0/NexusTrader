import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.bot_session import BotSession
from trading.executor import OrderExecutor
from trading.monitor import ContractMonitor
from trading.safety import RealTradingDisabled, ensure_demo_account


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.subscriptions = {}
        self.unsubscribed = []
        self.contract_responses = []

    async def send(self, request):
        self.sent.append(request)
        if "proposal_open_contract" in request and self.contract_responses:
            return self.contract_responses.pop(0)
        return {"buy": {"contract_id": 42}}

    async def subscribe(self, key, request, handler):
        self.subscriptions[key] = handler
        return "sub-42"

    async def unsubscribe(self, key):
        self.unsubscribed.append(key)


async def _done():
    return None


async def _wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0)


class DemoExecutionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_account_cannot_execute(self):
        from config.settings import settings
        previous = settings.ALLOW_REAL_TRADING
        settings.ALLOW_REAL_TRADING = False
        try:
            with self.assertRaises(RealTradingDisabled):
                ensure_demo_account({"account_type": "real"})

            connection = FakeConnection()
            executor = OrderExecutor(connection, account_type="real")
            with self.assertRaises(RealTradingDisabled):
                await executor.buy("proposal-id", 1.0)
            self.assertEqual(connection.sent, [])
        finally:
            settings.ALLOW_REAL_TRADING = previous

    async def test_demo_account_can_execute(self):
        connection = FakeConnection()
        executor = OrderExecutor(connection, account_type="demo")

        result = await executor.buy("proposal-id", 1.0)

        self.assertEqual(result["contract_id"], 42)
        self.assertEqual(connection.sent, [{"buy": "proposal-id", "price": 1.0}])


class ContractSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_settlement_callback_is_retried_on_next_sold_update(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        attempts = []

        async def on_settled(contract):
            attempts.append(contract["contract_id"])
            if len(attempts) == 1:
                raise RuntimeError("database temporarily unavailable")

        await monitor.monitor_contract(42, on_settled)
        callback = connection.subscriptions["contract:42"]
        sold = {
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "won",
                "profit": "0.95",
                "payout": "1.95",
            }
        }

        with self.assertRaises(RuntimeError):
            await callback(sold)
        await callback(sold)

        self.assertEqual(attempts, [42, 42])
        self.assertEqual(connection.unsubscribed, ["contract:42"])
        await monitor.close()

    async def test_duplicate_sold_update_settles_contract_once(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        settlements = []

        async def on_settled(contract):
            settlements.append(contract["contract_id"])

        await monitor.monitor_contract(42, on_settled)
        callback = connection.subscriptions["contract:42"]
        sold = {
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "won",
                "profit": "0.95",
                "payout": "1.95",
            }
        }
        await callback(sold)
        await callback(sold)

        self.assertEqual(settlements, [42])
        self.assertEqual(connection.unsubscribed, ["contract:42"])
        await monitor.close()

    async def test_open_contract_update_is_forwarded_for_live_chart(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        updates = []

        async def on_settled(contract):
            return None

        async def on_update(contract):
            updates.append(contract["current_spot"])

        await monitor.monitor_contract(42, on_settled, on_update_callback=on_update)
        callback = connection.subscriptions["contract:42"]
        await callback({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 0,
                "date_expiry": 4102444800,
                "status": "open",
                "current_spot": "100.25",
                "profit": "0.10",
                "payout": "1.95",
            }
        })

        self.assertEqual(updates, ["100.25"])
        await monitor.close()

    async def test_point_query_settles_when_subscription_misses_terminal_update(self):
        connection = FakeConnection()
        connection.contract_responses.append({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "lost",
                "profit": "-1.00",
                "payout": "0",
            }
        })
        settled = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        await monitor.monitor_contract(
            42,
            lambda contract: settled.append(contract) or _done(),
        )
        callback = connection.subscriptions["contract:42"]
        await callback({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "open",
                "profit": "-1.00",
                "payout": "0",
            }
        })

        await asyncio.wait_for(_wait_until(lambda: len(settled) == 1), timeout=0.5)

        self.assertEqual(settled[0]["status"], "lost")
        self.assertEqual(connection.unsubscribed, ["contract:42"])
        await monitor.close()

    async def test_expired_unsold_query_keeps_reconciling_until_sold(self):
        connection = FakeConnection()
        connection.contract_responses.extend([
            {
                "proposal_open_contract": {
                    "contract_id": 42,
                    "contract_type": "CALL",
                    "currency": "USD",
                    "is_sold": 0,
                    "is_expired": 1,
                    "date_expiry": 1,
                    "status": "open",
                    "profit": "-1.00",
                    "payout": "0",
                }
            },
            {
                "proposal_open_contract": {
                    "contract_id": 42,
                    "contract_type": "CALL",
                    "currency": "USD",
                    "is_sold": 1,
                    "is_expired": 1,
                    "date_expiry": 1,
                    "status": "lost",
                    "profit": "-1.00",
                    "payout": "0",
                }
            },
        ])
        settled = []
        forwarded_updates = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        await monitor.monitor_contract(
            42,
            lambda contract: settled.append(contract) or _done(),
            on_update_callback=lambda contract: forwarded_updates.append(
                contract["is_sold"]
            ) or _done(),
        )
        await connection.subscriptions["contract:42"]({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "open",
                "profit": "-1.00",
                "payout": "0",
            }
        })

        await asyncio.wait_for(_wait_until(lambda: len(settled) == 1), timeout=0.5)

        queries = [
            item for item in connection.sent if "proposal_open_contract" in item
        ]
        self.assertEqual(len(queries), 2)
        self.assertEqual(settled[0]["is_sold"], 1)
        self.assertEqual(forwarded_updates, [0, 0])
        await monitor.close()

    async def test_subscription_and_query_terminal_updates_settle_once(self):
        sold = {
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "won",
                "profit": "0.95",
                "payout": "1.95",
            }
        }

        class BlockingConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.query_started = asyncio.Event()
                self.release_query = asyncio.Event()

            async def send(self, request):
                self.sent.append(request)
                self.query_started.set()
                await self.release_query.wait()
                return sold

        connection = BlockingConnection()
        settlements = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        await monitor.monitor_contract(
            42,
            lambda contract: settlements.append(contract["contract_id"]) or _done(),
        )
        callback = connection.subscriptions["contract:42"]
        await callback({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "open",
                "profit": "0",
                "payout": "1.95",
            }
        })
        await asyncio.wait_for(connection.query_started.wait(), timeout=0.5)

        await callback(sold)
        connection.release_query.set()
        await asyncio.sleep(0)

        self.assertEqual(settlements, [42])
        self.assertEqual(connection.unsubscribed, ["contract:42"])
        await monitor.close()

    async def test_missing_expiry_starts_fallback_after_interval(self):
        connection = FakeConnection()
        connection.contract_responses.append({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "lost",
                "profit": "-1.00",
                "payout": "0",
            }
        })
        settlements = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        await monitor.monitor_contract(
            42,
            lambda contract: settlements.append(contract["contract_id"]) or _done(),
        )

        await asyncio.wait_for(
            _wait_until(lambda: settlements == [42]),
            timeout=0.5,
        )

        self.assertIn(
            {"proposal_open_contract": 1, "contract_id": 42},
            connection.sent,
        )
        await monitor.close()

    async def test_close_cancels_reconciliation_without_settlement(self):
        class BlockingConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.query_started = asyncio.Event()
                self.never = asyncio.Event()

            async def send(self, request):
                self.sent.append(request)
                self.query_started.set()
                await self.never.wait()

        connection = BlockingConnection()
        settlements = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        await monitor.monitor_contract(
            42,
            lambda contract: settlements.append(contract) or _done(),
        )
        await asyncio.wait_for(connection.query_started.wait(), timeout=0.5)

        await asyncio.wait_for(monitor.close(), timeout=0.5)

        self.assertEqual(settlements, [])
        self.assertEqual(connection.unsubscribed, ["contract:42"])

    async def test_close_coordinates_with_inflight_subscription(self):
        class BlockingSubscribeConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.subscribe_started = asyncio.Event()
                self.release_subscribe = asyncio.Event()
                self.active_subscriptions = set()

            async def subscribe(self, key, request, handler):
                self.subscribe_started.set()
                await self.release_subscribe.wait()
                self.subscriptions[key] = handler
                self.active_subscriptions.add(key)
                return "sub-42"

            async def unsubscribe(self, key):
                self.unsubscribed.append(key)
                self.active_subscriptions.discard(key)

        connection = BlockingSubscribeConnection()
        monitor = ContractMonitor(connection)
        monitoring = asyncio.create_task(
            monitor.monitor_contract(42, _done)
        )
        await asyncio.wait_for(connection.subscribe_started.wait(), timeout=0.5)

        closing = asyncio.create_task(monitor.close())
        await asyncio.sleep(0)
        connection.release_subscribe.set()
        await asyncio.wait_for(
            asyncio.gather(monitoring, closing),
            timeout=0.5,
        )

        self.assertEqual(connection.active_subscriptions, set())
        self.assertEqual(connection.unsubscribed, ["contract:42"])

    async def test_close_retries_unsubscribe_after_transient_failure(self):
        class FlakyUnsubscribeConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.unsubscribe_attempts = 0

            async def unsubscribe(self, key):
                self.unsubscribe_attempts += 1
                if self.unsubscribe_attempts == 1:
                    raise RuntimeError("temporary unsubscribe failure")
                self.unsubscribed.append(key)

        connection = FlakyUnsubscribeConnection()
        monitor = ContractMonitor(connection)
        await monitor.monitor_contract(42, _done)
        errors = []

        for _ in range(2):
            try:
                await monitor.close()
            except RuntimeError as exc:
                errors.append(str(exc))

        self.assertEqual(errors, [])
        self.assertEqual(connection.unsubscribe_attempts, 2)
        self.assertEqual(connection.unsubscribed, ["contract:42"])

    async def test_late_open_payload_after_settlement_is_ignored(self):
        connection = FakeConnection()
        monitor = ContractMonitor(connection)
        settlements = []
        updates = []
        await monitor.monitor_contract(
            42,
            lambda contract: settlements.append(contract["contract_id"]) or _done(),
            on_update_callback=lambda contract: updates.append(
                contract["current_spot"]
            ) or _done(),
        )
        callback = connection.subscriptions["contract:42"]
        await callback({
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "won",
                "current_spot": "101.00",
                "profit": "0.95",
                "payout": "1.95",
            }
        })

        await callback({
            "subscription": {"id": "sub-42"},
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "open",
                "current_spot": "100.50",
                "profit": "0.50",
                "payout": "1.95",
            },
        })

        self.assertEqual(settlements, [42])
        self.assertEqual(updates, [])
        await monitor.close()

    async def test_point_response_routed_to_subscription_is_forwarded_once(self):
        point_response = {
            "req_id": 77,
            "msg_type": "proposal_open_contract",
            "proposal_open_contract": {
                "contract_id": 42,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 0,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "open",
                "current_spot": "100.10",
                "profit": "-0.10",
                "payout": "1.95",
            },
        }

        class RoutedPointConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.second_query_started = asyncio.Event()
                self.never = asyncio.Event()

            async def send(self, request):
                self.sent.append(request)
                if len(self.sent) == 1:
                    await self.subscriptions["contract:42"](point_response)
                    return point_response
                self.second_query_started.set()
                await self.never.wait()

        connection = RoutedPointConnection()
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=0.01,
            expiry_grace_seconds=0,
        )
        updates = []
        settlements = []
        await monitor.monitor_contract(
            42,
            lambda contract: settlements.append(contract["contract_id"]) or _done(),
            on_update_callback=lambda contract: updates.append(
                contract["current_spot"]
            ) or _done(),
        )
        callback = connection.subscriptions["contract:42"]

        try:
            await asyncio.wait_for(
                connection.second_query_started.wait(),
                timeout=0.5,
            )
            self.assertEqual(updates, ["100.10"])

            await callback({
                "req_id": 77,
                "msg_type": "proposal_open_contract",
                "subscription": {"id": "sub-42"},
                "proposal_open_contract": {
                    "contract_id": 42,
                    "contract_type": "CALL",
                    "currency": "USD",
                    "is_sold": 0,
                    "is_expired": 1,
                    "date_expiry": 1,
                    "status": "open",
                    "current_spot": "100.20",
                    "profit": "-0.05",
                    "payout": "1.95",
                },
            })
            self.assertEqual(updates, ["100.10", "100.20"])

            await callback({
                "req_id": 77,
                "msg_type": "proposal_open_contract",
                "subscription": {"id": "sub-42"},
                "proposal_open_contract": {
                    "contract_id": 42,
                    "contract_type": "CALL",
                    "currency": "USD",
                    "is_sold": 1,
                    "is_expired": 1,
                    "date_expiry": 1,
                    "status": "won",
                    "current_spot": "101.00",
                    "profit": "0.95",
                    "payout": "1.95",
                },
            })
            self.assertEqual(settlements, [42])
        finally:
            await monitor.close()


class CrashRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_wait_has_a_bounded_failure_state(self):
        class Repository:
            async def touch_bot_heartbeat(self, bot_id):
                return None

        class Publisher:
            async def publish(self, event):
                return True

        session = BotSession(Repository(), {"id": "bot-a"}, publisher=Publisher())
        session._active_contracts.add(42)

        with self.assertRaises(TimeoutError):
            await session._wait_for_active_contracts(timeout_seconds=0.01)

    async def test_db_owned_contract_is_resubscribed_even_when_portfolio_is_empty(self):
        class Repository:
            def __init__(self):
                self.upserts = []

            async def list_trades(self, bot_id, limit=1000):
                return [{
                    "bot_id": bot_id, "contract_id": 42, "contract_type": "CALL",
                    "symbol": "R_75", "stake": 1.0, "status": "open",
                }]

            async def upsert_trade(self, trade):
                self.upserts.append(trade)

        class Publisher:
            async def publish(self, event):
                return True

        class Monitor:
            def __init__(self):
                self.contracts = []

            async def monitor_contract(self, contract_id, on_settled, on_update_callback=None):
                self.contracts.append(contract_id)

        class Strategy:
            def name(self):
                return "Donchian+ZigZag"

        repository = Repository()
        session = BotSession(repository, {
            "id": "bot-a", "symbol": "R_75", "initial_stake": 1.0,
        }, publisher=Publisher())
        session._connection = object()
        monitor = Monitor()

        with patch("core.bot_session.CrashRecoveryHandler") as recovery_type:
            recovery_type.return_value.check_open_contracts = AsyncMock(return_value=[])
            await session._recover_owned_contracts(monitor, Strategy(), object())

        self.assertEqual(monitor.contracts, [42])
        self.assertEqual(session._active_contracts, {42})
        self.assertEqual(repository.upserts, [])


if __name__ == "__main__":
    unittest.main()
