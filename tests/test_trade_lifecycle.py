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


class BotSessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.strategy = type(
            "Strategy",
            (),
            {"name": lambda self: "Donchian+ZigZag"},
        )()
        self.session = BotSession(
            object(),
            {"id": "bot-a", "symbol": "R_75"},
            publisher=object(),
        )

    def test_expired_unsold_payload_is_awaiting_settlement(self):
        payload = self.session._trade_payload({
            "contract_id": 42,
            "contract_type": "CALL",
            "underlying": "R_75",
            "is_sold": 0,
            "is_expired": 1,
            "status": "open",
            "profit": "-1.00",
            "buy_price": "1.00",
            "payout": "0",
            "date_expiry": 100,
            "date_settlement": None,
        }, self.strategy, "open")

        self.assertEqual(payload["status"], "open")
        self.assertEqual(payload["lifecycle_state"], "awaiting_settlement")
        self.assertFalse(payload["is_sold"])
        self.assertTrue(payload["is_expired"])
        self.assertIsNone(payload["date_settlement"])

    def test_unexpired_unsold_payload_is_live(self):
        payload = self.session._trade_payload({
            "contract_id": 42,
            "contract_type": "CALL",
            "underlying": "R_75",
            "is_sold": 0,
            "is_expired": 0,
            "status": "open",
            "profit": "0.10",
            "buy_price": "1.00",
            "payout": "1.95",
            "date_expiry": 200,
        }, self.strategy, "open")

        self.assertEqual(payload["lifecycle_state"], "live")
        self.assertFalse(payload["is_sold"])
        self.assertFalse(payload["is_expired"])

    def test_sold_payload_is_closed_and_keeps_settlement_timestamp(self):
        payload = self.session._trade_payload({
            "contract_id": 42,
            "contract_type": "CALL",
            "underlying": "R_75",
            "is_sold": 1,
            "is_expired": 1,
            "status": "won",
            "profit": "0.95",
            "buy_price": "1.00",
            "payout": "1.95",
            "date_expiry": 100,
            "date_settlement": 101,
        }, self.strategy, "open")

        self.assertEqual(payload["status"], "open")
        self.assertEqual(payload["lifecycle_state"], "closed")
        self.assertTrue(payload["is_sold"])
        self.assertTrue(payload["is_expired"])
        self.assertEqual(payload["date_settlement"], 101)

    async def test_open_trade_is_created_with_live_lifecycle(self):
        class Repository:
            def __init__(self):
                self.trades = []

            async def upsert_trade(self, trade):
                self.trades.append(trade)

        class Publisher:
            async def publish(self, event):
                return True

        class Monitor:
            async def monitor_contract(
                self,
                contract_id,
                on_settled,
                on_update_callback=None,
            ):
                return None

        repository = Repository()
        session = BotSession(
            repository,
            {"id": "bot-a", "symbol": "R_75", "initial_stake": 1.0},
            publisher=Publisher(),
        )

        await session._register_contract(
            {
                "contract_id": 42,
                "buy_price": "1.00",
                "payout": "1.95",
                "date_expiry": 100,
            },
            "CALL",
            self.strategy,
            Monitor(),
            object(),
        )

        self.assertEqual(repository.trades[0]["status"], "open")
        self.assertEqual(repository.trades[0]["lifecycle_state"], "live")
        self.assertFalse(repository.trades[0]["is_sold"])
        self.assertFalse(repository.trades[0]["is_expired"])
        self.assertIsNone(repository.trades[0]["date_settlement"])

    async def test_run_closes_monitor_before_market_and_connection(self):
        events = []

        class Repository:
            async def create_session(self, session_id):
                return None

            async def set_runtime_state(self, bot_id, status, error=None):
                return None

            async def close_session(self, session_id, status="closed"):
                return None

            async def list_trades(self, bot_id, limit=1000):
                return []

        class Publisher:
            async def start(self):
                return None

            async def publish(self, event):
                return True

        class Auth:
            async def list_accounts(self):
                return [{
                    "account_id": "DOT-DEMO",
                    "account_type": "demo",
                    "status": "active",
                }]

            async def close(self):
                events.append("auth")

        class Connection:
            def __init__(self, auth):
                self.auth = auth

            async def connect(self, account_id):
                return True

            async def disconnect(self):
                events.append("connection")

        class Market:
            def __init__(self, *args, **kwargs):
                pass

            async def start(self, symbol, timeframe):
                return None

            async def close(self):
                events.append("market")

        class Monitor:
            def __init__(self, connection):
                pass

            async def close(self):
                events.append("monitor")

        class CompletedSession(BotSession):
            async def _trade_loop(self, *args):
                return None

        bot = {
            "id": "bot-a",
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "strategy_id": "donchian",
            "symbol": "R_75",
            "timeframe_seconds": 60,
            "duration": 2,
            "duration_unit": "m",
            "initial_stake": 1.0,
        }
        auth = Auth()
        with patch("core.bot_session.AuthManager", return_value=auth), \
             patch("core.bot_session.NexusConnection", Connection), \
             patch("core.bot_session.MarketDataHandler", Market), \
             patch("core.bot_session.ContractMonitor", Monitor):
            await CompletedSession(
                Repository(),
                bot,
                publisher=Publisher(),
            ).run()

        self.assertEqual(events, ["monitor", "market", "connection"])

    async def test_run_closes_created_monitor_after_partial_initialization_failure(self):
        events = []

        class Repository:
            async def create_session(self, session_id):
                return None

            async def set_runtime_state(self, bot_id, status, error=None):
                return None

            async def close_session(self, session_id, status="closed"):
                return None

        class Publisher:
            async def start(self):
                return None

            async def publish(self, event):
                return True

        class Auth:
            async def list_accounts(self):
                return [{
                    "account_id": "DOT-DEMO",
                    "account_type": "demo",
                    "status": "active",
                }]

        class Connection:
            def __init__(self, auth):
                pass

            async def connect(self, account_id):
                return True

            async def disconnect(self):
                events.append("connection")

        class Monitor:
            def __init__(self, connection):
                pass

            async def close(self):
                events.append("monitor")

        bot = {
            "id": "bot-a",
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "strategy_id": "donchian",
            "symbol": "R_75",
            "timeframe_seconds": 60,
            "duration": 2,
            "duration_unit": "m",
            "initial_stake": 1.0,
        }
        with patch("core.bot_session.AuthManager", return_value=Auth()), \
             patch("core.bot_session.NexusConnection", Connection), \
             patch("core.bot_session.ContractMonitor", Monitor), \
             patch(
                 "core.bot_session.MarketDataHandler",
                 side_effect=RuntimeError("market initialization failed"),
             ):
            with self.assertRaisesRegex(
                RuntimeError,
                "market initialization failed",
            ):
                await BotSession(
                    Repository(),
                    bot,
                    publisher=Publisher(),
                ).run()

        self.assertEqual(events, ["monitor", "connection"])


class ContractSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_expiry_reconciles_even_without_initial_stream_payload(self):
        connection = FakeConnection()
        connection.contract_responses.append({
            "proposal_open_contract": {
                "contract_id": 43,
                "contract_type": "CALL",
                "currency": "USD",
                "is_sold": 1,
                "is_expired": 1,
                "date_expiry": 1,
                "status": "lost",
                "profit": "-0.35",
                "payout": "0",
            }
        })
        settlements = []
        monitor = ContractMonitor(
            connection,
            reconcile_interval_seconds=30,
            expiry_grace_seconds=0,
        )

        await monitor.monitor_contract(
            43,
            lambda contract: settlements.append(contract["contract_id"]) or _done(),
            expected_expiry_epoch=1,
        )

        await asyncio.wait_for(_wait_until(lambda: settlements == [43]), timeout=0.5)
        self.assertIn(
            {"proposal_open_contract": 1, "contract_id": 43},
            connection.sent,
        )
        await monitor.close()

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
