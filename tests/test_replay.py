import unittest

from backtest.engine import ReplayEngine
from backtest.cli import strategy_factory
from backtest.metrics import calculate_metrics
from strategies.base import Signal
from strategies.nexus_speed import NexusSpeedStrategy


class OneShotStrategy:
    def __init__(self):
        self.sent = False
        self.results = []

    def analyze(self, ticks, candles=None):
        assert all(candle["time"] <= ticks[-1]["epoch"] for candle in candles)
        if not self.sent:
            self.sent = True
            return Signal("CALL", "fixture", ticks[-1]["quote"], ticks[-1]["epoch"])
        return None

    def get_contract_params(self):
        return {"duration": 2, "duration_unit": "m"}

    def get_stake(self):
        return 1.0

    def on_trade_result(self, contract):
        self.results.append(contract)


class TenSecondStrategy(OneShotStrategy):
    min_profit_ratio = 0.87

    def analyze(self, ticks, candles=None):
        if not self.sent:
            self.sent = True
            current = ticks[-1]
            return Signal(
                "CALL",
                "ten-second-fixture",
                current["quote"],
                current["epoch"],
                tick_sequence=current["sequence"],
                candle_time=current["epoch"] // 60 * 60,
            )
        return None

    def get_contract_params(self):
        return {"duration": 10, "duration_unit": "s"}


class DeterministicReplayTests(unittest.TestCase):
    def test_cli_exposes_nexus_speed_replay(self):
        self.assertIs(strategy_factory("nexus_speed"), NexusSpeedStrategy)

    def test_replay_is_deterministic_and_uses_first_tick_at_or_after_expiry(self):
        ticks = [
            {"epoch": 0, "quote": 100.0, "payout_ratio": 0.8},
            {"epoch": 60, "quote": 99.0, "payout_ratio": 0.8},
            {"epoch": 121, "quote": 101.0, "payout_ratio": 0.8},
        ]

        first = ReplayEngine(strategy_factory=OneShotStrategy).run(ticks)
        second = ReplayEngine(strategy_factory=OneShotStrategy).run(ticks)

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "COMPLETE")
        self.assertEqual(first["manifest"]["sha256"], second["manifest"]["sha256"])
        self.assertEqual(first["trades"][0]["exit_epoch"], 121)
        self.assertEqual(first["trades"][0]["profit"], 0.8)

    def test_missing_expiry_tick_marks_run_incomplete_without_fabricated_result(self):
        result = ReplayEngine(strategy_factory=OneShotStrategy).run([
            {"epoch": 0, "quote": 100.0, "payout_ratio": 0.8},
            {"epoch": 60, "quote": 101.0, "payout_ratio": 0.8},
        ])

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["metrics"]["invalid_trades"], 1)
        self.assertEqual(result["metrics"]["wins"], 0)
        self.assertEqual(result["metrics"]["losses"], 0)

    def test_out_of_order_dataset_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ordem"):
            ReplayEngine(strategy_factory=OneShotStrategy).run([
                {"epoch": 60, "quote": 100}, {"epoch": 30, "quote": 101},
            ])

    def test_ten_second_replay_enters_on_next_tick_and_allows_equal_epochs(self):
        result = ReplayEngine(strategy_factory=TenSecondStrategy).run([
            {"epoch": 0, "quote": 100.0, "payout_ratio": 0.90},
            {"epoch": 0, "quote": 101.0, "payout_ratio": 0.90},
            {"epoch": 9, "quote": 100.5, "payout_ratio": 0.90},
            {"epoch": 10, "quote": 102.0, "payout_ratio": 0.90},
        ])

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["manifest"]["duration_seconds"], 10)
        self.assertEqual(result["trades"][0]["entry_epoch"], 0)
        self.assertEqual(result["trades"][0]["entry_spot"], 101.0)
        self.assertEqual(result["trades"][0]["exit_epoch"], 10)
        self.assertEqual(result["trades"][0]["profit"], 0.9)

    def test_ten_second_replay_requires_payout_on_entry_tick(self):
        result = ReplayEngine(strategy_factory=TenSecondStrategy).run([
            {"epoch": 0, "quote": 100.0, "payout_ratio": 0.90},
            {"epoch": 1, "quote": 101.0},
            {"epoch": 11, "quote": 102.0, "payout_ratio": 0.90},
        ])

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["metrics"]["invalid_trades"], 1)
        self.assertEqual(
            result["trades"][0]["invalid_reason"], "missing_payout_ratio"
        )

    def test_ten_second_replay_rejects_entry_below_profit_floor(self):
        result = ReplayEngine(strategy_factory=TenSecondStrategy).run([
            {"epoch": 0, "quote": 100.0, "payout_ratio": 0.90},
            {"epoch": 1, "quote": 101.0, "payout_ratio": 0.86},
            {"epoch": 11, "quote": 102.0, "payout_ratio": 0.90},
        ])

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["rejected_signals"][0]["reason"], "profit_ratio_below_minimum")


class ReplayMetricTests(unittest.TestCase):
    def test_financial_metrics_include_expectancy_drawdown_and_streak(self):
        metrics = calculate_metrics([
            {"status": "won", "profit": 0.8, "stake": 1.0, "payout_ratio": 0.8},
            {"status": "lost", "profit": -1.0, "stake": 1.0, "payout_ratio": 0.8},
            {"status": "lost", "profit": -1.0, "stake": 1.0, "payout_ratio": 0.8},
            {"status": "won", "profit": 0.8, "stake": 1.0, "payout_ratio": 0.8},
        ])

        self.assertEqual(metrics["trades"], 4)
        self.assertEqual(metrics["wins"], 2)
        self.assertEqual(metrics["losses"], 2)
        self.assertAlmostEqual(metrics["expectancy"], -0.1)
        self.assertAlmostEqual(metrics["profit_factor"], 0.8)
        self.assertEqual(metrics["max_loss_streak"], 2)
        self.assertAlmostEqual(metrics["max_drawdown_absolute"], 2.0)
        self.assertIn("equity_curve", metrics)


if __name__ == "__main__":
    unittest.main()
