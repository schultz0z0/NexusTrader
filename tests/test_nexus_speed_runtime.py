import unittest

from core.bot_session import BotSession
from strategies.base import Signal
from strategies.nexus_speed import IndicatorSnapshot, NexusSpeedStrategy
from trading.proposal import ProposalManager


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def send(self, request):
        self.requests.append(request)
        return self.response


class NexusSpeedRuntimeTests(unittest.TestCase):
    def _session(self, **overrides):
        bot = {
            "id": "nexus-speed-a",
            "strategy_id": "nexus_speed",
            "initial_stake": 1.0,
            "money_management": "fixed",
            "duration": 5,
            "duration_unit": "t",
        }
        bot.update(overrides)
        return BotSession(object(), bot, publisher=object())

    def test_builds_nexus_speed_with_fixed_five_tick_contract(self):
        strategy = self._session()._build_strategy()

        self.assertIsInstance(strategy, NexusSpeedStrategy)
        self.assertEqual(
            strategy.get_contract_params(),
            {"duration": 5, "duration_unit": "t"},
        )

    def test_runtime_uses_persisted_adx_threshold(self):
        strategy = self._session(
            strategy_config={"adx_threshold": 25}
        )._build_strategy()

        self.assertEqual(strategy.adx_threshold, 25.0)

    def test_runtime_rejects_non_integer_persisted_adx_thresholds(self):
        for threshold in (25.5, "25", True):
            with self.subTest(threshold=threshold), self.assertRaisesRegex(
                ValueError, "20, 25 ou 30"
            ):
                self._session(
                    strategy_config={"adx_threshold": threshold}
                )._build_strategy()

    def test_rejects_non_five_tick_nexus_configuration(self):
        with self.assertRaisesRegex(ValueError, "5 ticks"):
            self._session(duration=10)._build_strategy()

    def test_rejects_profit_floor_below_eighty_seven_percent(self):
        with self.assertRaisesRegex(ValueError, "0.87"):
            self._session(
                strategy_config={"min_profit_ratio": 0.86}
            )._build_strategy()

    def test_blocks_profit_below_eighty_seven_percent(self):
        strategy = self._session()._build_strategy()
        signal = Signal(
            "CALL", "fixture", 100.0, 1000, tick_sequence=10, candle_time=960
        )

        reason = self._session()._nexus_trade_block_reason(
            strategy,
            signal,
            {"ask_price": "1.00", "payout": "1.869"},
            {"sequence": 11},
        )

        self.assertEqual(reason, "profit_ratio_below_minimum")

    def test_accepts_exactly_eighty_seven_percent(self):
        strategy = self._session()._build_strategy()
        signal = Signal(
            "PUT", "fixture", 100.0, 1000, tick_sequence=10, candle_time=960
        )

        reason = self._session()._nexus_trade_block_reason(
            strategy,
            signal,
            {"ask_price": "1.00", "payout": "1.87"},
            {"sequence": 11},
        )

        self.assertIsNone(reason)

    def test_blocks_signal_delayed_by_more_than_one_tick(self):
        strategy = self._session()._build_strategy()
        signal = Signal(
            "PUT", "fixture", 100.0, 1000, tick_sequence=10, candle_time=960
        )

        reason = self._session()._nexus_trade_block_reason(
            strategy,
            signal,
            {"ask_price": "1.00", "payout": "1.90"},
            {"sequence": 12},
        )

        self.assertEqual(reason, "signal_stale_by_ticks")


class ProposalValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_profit_ratio_uses_net_profit_over_ask_price(self):
        self.assertAlmostEqual(
            ProposalManager.profit_ratio({"ask_price": "2.00", "payout": "3.80"}),
            0.90,
        )

    def test_profit_ratio_rejects_missing_or_invalid_values(self):
        self.assertIsNone(ProposalManager.profit_ratio({"ask_price": "1.00"}))
        self.assertIsNone(
            ProposalManager.profit_ratio({"ask_price": "0", "payout": "1.90"})
        )

    async def test_contract_preflight_requires_call_and_put(self):
        connection = FakeConnection(
            {
                "contracts_for": {
                    "available": [
                        {"contract_type": "CALL"},
                        {"contract_type": "PUT"},
                    ]
                }
            }
        )
        manager = ProposalManager(connection)

        result = await manager.validate_contract_types("R_100", {"CALL", "PUT"})

        self.assertTrue(result)
        self.assertEqual(connection.requests, [{"contracts_for": "R_100"}])

    async def test_contract_preflight_rejects_missing_direction(self):
        connection = FakeConnection(
            {"contracts_for": {"available": [{"contract_type": "CALL"}]}}
        )
        manager = ProposalManager(connection)

        with self.assertRaisesRegex(ValueError, "PUT"):
            await manager.validate_contract_types("R_100", {"CALL", "PUT"})

    async def test_duration_preflight_quotes_both_directions_at_five_ticks(self):
        connection = FakeConnection(
            {
                "proposal": {
                    "id": "proposal-a",
                    "ask_price": "1.00",
                    "payout": "1.90",
                }
            }
        )
        manager = ProposalManager(connection)

        result = await manager.validate_fixed_duration(
            "R_100", {"CALL", "PUT"}, 1.0, 5, "t"
        )

        self.assertTrue(result)
        self.assertEqual({item["contract_type"] for item in connection.requests}, {"CALL", "PUT"})
        self.assertTrue(all(item["duration"] == 5 for item in connection.requests))
        self.assertTrue(all(item["duration_unit"] == "t" for item in connection.requests))


class RecordingPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return True


class NexusSpeedRuntimeTelemetryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _candles(opening):
        return [
            {"time": 0, "open": 97, "high": 99, "low": 96, "close": 98},
            {"time": 60, "open": 98, "high": 100, "low": 97, "close": 99},
            {"time": 120, "open": 99, "high": 101, "low": 98, "close": 100},
            {
                "time": 180,
                "open": opening,
                "high": opening,
                "low": opening,
                "close": opening,
            },
        ]

    async def test_runtime_publishes_transitions_and_signal_coordinates(self):
        publisher = RecordingPublisher()
        session = BotSession(
            object(),
            {
                "id": "nexus-speed-a",
                "strategy_id": "nexus_speed",
                "duration": 5,
                "duration_unit": "t",
            },
            publisher=publisher,
        )
        strategy = NexusSpeedStrategy(
            min_closed_candles=3,
            indicator_provider=lambda _: IndicatorSnapshot(100, 99, 31, 20),
        )
        startup = {
            "sequence": 1,
            "epoch": 121,
            "quote": 100.0,
            "pip_size": 2,
            "is_live": True,
        }
        opening = {
            "sequence": 2,
            "epoch": 181,
            "quote": 110.0,
            "pip_size": 2,
            "is_live": True,
        }
        strategy.analyze([startup], self._candles(100.0)[:-1])
        strategy.analyze([startup, opening], self._candles(110.0))

        await session._publish_nexus_transitions(strategy)
        signal = Signal(
            "CALL",
            "fixture",
            100.02,
            191,
            tick_sequence=4,
            candle_time=180,
        )
        await session._publish_strategy_signal(signal)

        transition = publisher.events[0]
        self.assertEqual(transition["type"], "strategy.transition")
        self.assertEqual(transition["phase"], "qualification")
        self.assertEqual(transition["reason_code"], "filters_passed")
        self.assertEqual(transition["tick_sequence"], 2)
        self.assertEqual(transition["candle_time"], 180)
        signal_event = publisher.events[-1]
        self.assertEqual(signal_event["type"], "strategy.signal")
        self.assertEqual(signal_event["tick_sequence"], 4)
        self.assertEqual(signal_event["candle_time"], 180)
