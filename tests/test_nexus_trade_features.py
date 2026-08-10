import unittest

from stock_indicators import indicators

from nexus_trade.features import FeatureBuilder
from nexus_trade.indicators import IndicatorEngine
from utils.indicator_quotes import candles_to_quotes


def candles(count=80):
    return [
        {
            "time": index * 60,
            "open": 100.0 + index * 0.17 + (index % 3) * 0.03,
            "high": 100.8 + index * 0.17 + (index % 4) * 0.02,
            "low": 99.4 + index * 0.17 - (index % 2) * 0.03,
            "close": 100.1 + index * 0.17 + ((index % 5) - 2) * 0.04,
        }
        for index in range(count)
    ]


class IndicatorEngineTests(unittest.TestCase):
    def test_bollinger_and_adx_match_stock_indicators_and_ignore_the_live_candle(self):
        closed = candles()
        live = {
            "time": len(closed) * 60,
            "open": 1.0,
            "high": 9_999.0,
            "low": 0.001,
            "close": 9_000.0,
            "is_closed": False,
        }
        frames = IndicatorEngine().calculate([*closed, live])
        expected_bollinger = indicators.get_bollinger_bands(candles_to_quotes(closed), 20, 2)[-1]
        expected_adx = indicators.get_adx(candles_to_quotes(closed), 14)[-1]
        last = frames[-1]

        self.assertEqual(len(frames), len(closed))
        self.assertEqual(last.epoch, closed[-1]["time"])
        self.assertAlmostEqual(last.upper, expected_bollinger.upper_band, places=10)
        self.assertAlmostEqual(last.middle, expected_bollinger.sma, places=10)
        self.assertAlmostEqual(last.lower, expected_bollinger.lower_band, places=10)
        self.assertAlmostEqual(last.adx, expected_adx.adx, places=10)
        self.assertAlmostEqual(last.values["bollinger_percent_b"], expected_bollinger.percent_b, places=10)
        self.assertAlmostEqual(last.values["bollinger_z_score"], expected_bollinger.z_score, places=10)

    def test_warmup_values_remain_none_without_future_filling(self):
        frames = IndicatorEngine().calculate(candles(19))

        self.assertIsNone(frames[0].upper)
        self.assertIsNone(frames[0].middle)
        self.assertIsNone(frames[0].lower)
        self.assertIsNone(frames[0].adx)
        self.assertIsNone(frames[-1].upper)
        self.assertIsNone(frames[-1].values["bollinger_slope"])


class FeatureBuilderTests(unittest.TestCase):
    def test_build_emits_the_full_causal_feature_repertoire(self):
        frames = FeatureBuilder().build(candles())
        values = frames[-1].values

        self.assertEqual(len(frames), 80)
        for feature in {
            "bollinger_percent_b", "bollinger_z_score", "bollinger_width", "bollinger_slope",
            "chop", "atr", "atrp", "rsi", "stoch_k", "stoch_d", "cci",
            "keltner_upper", "keltner_center", "keltner_lower", "roc", "aroon_up", "aroon_down",
            "sma", "ema", "wma", "hma", "kama", "body", "body_ratio",
            "upper_wick", "lower_wick", "upper_wick_ratio", "lower_wick_ratio",
        }:
            with self.subTest(feature=feature):
                self.assertIn(feature, values)
        self.assertIsNone(frames[0].values["rsi"])
        self.assertIsNone(frames[0].values["hma"])


if __name__ == "__main__":
    unittest.main()
