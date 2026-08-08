import unittest

from core.bot_session import BotSession
from strategies.base import Signal
from strategies.nexus_speed import NexusSpeedStrategy
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
