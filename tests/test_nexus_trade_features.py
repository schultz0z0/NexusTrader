import unittest

from stock_indicators import indicators

from nexus_trade.features import FeatureBuilder
from nexus_trade.indicators import IndicatorEngine, closed_candles
from utils.indicator_quotes import candles_to_quotes


def candles(count=80):
    return [
        {
            "time": index * 60,
            "is_closed": True,
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

    def test_bollinger_and_adx_match_stock_indicators_at_multiple_warmup_and_ready_indices(self):
        closed = candles()
        frames = IndicatorEngine().calculate(closed)
        expected_bollinger = indicators.get_bollinger_bands(candles_to_quotes(closed), 20, 2)
        expected_adx = indicators.get_adx(candles_to_quotes(closed), 14)

        for index in (0, 18, 19, 27, 40, 79):
            with self.subTest(index=index):
                self.assertEqual(frames[index].upper, expected_bollinger[index].upper_band)
                self.assertEqual(frames[index].middle, expected_bollinger[index].sma)
                self.assertEqual(frames[index].lower, expected_bollinger[index].lower_band)
                self.assertEqual(frames[index].adx, expected_adx[index].adx)

    def test_ambiguous_candle_is_rejected_without_closure_evidence_or_causal_cutoff(self):
        ambiguous = [{key: value for key, value in candle.items() if key != "is_closed"} for candle in candles(2)]

        with self.assertRaises(ValueError):
            IndicatorEngine().calculate(ambiguous)

    def test_explicit_causal_cutoff_accepts_only_prior_ambiguous_candles(self):
        ambiguous = [{key: value for key, value in candle.items() if key != "is_closed"} for candle in candles(3)]

        completed = closed_candles(ambiguous, active_candle_time=120)

        self.assertEqual([candle["time"] for candle in completed], [0, 60])

    def test_decision_inside_active_bucket_excludes_even_explicitly_closed_active_and_future_candles(self):
        completed = closed_candles(candles(3), decision_epoch=90)

        self.assertEqual([candle["time"] for candle in completed], [0])

    def test_close_epoch_must_be_an_expected_m1_close_not_after_the_decision(self):
        invalid = [
            {**candles(1)[0], "close_epoch": 59},
            {**candles(1)[0], "close_epoch": 60.0},
            {**candles(1)[0], "close_epoch": True},
            {**candles(1)[0], "close_epoch": 120},
            {**candles(2)[1], "close_epoch": 120},
        ]

        for candle in invalid:
            with self.subTest(candle=candle), self.assertRaises(ValueError):
                closed_candles([candle], decision_epoch=90)

    def test_decision_prefix_is_unchanged_when_active_and_future_candles_are_marked_closed(self):
        history = candles(40)
        marked_closed_active = {**candles(41)[40], "is_closed": True}

        before = IndicatorEngine().calculate(history, decision_epoch=2_400)
        after = IndicatorEngine().calculate([*history, marked_closed_active], decision_epoch=2_400)

        self.assertEqual(before, after)

    def test_indicator_engine_prefers_decision_epoch_when_runtime_supplies_both_cutoffs(self):
        history = candles(4)

        frames = IndicatorEngine().calculate(
            history,
            decision_epoch=150,
            active_candle_time=60,
        )

        self.assertEqual([frame.epoch for frame in frames], [0, 60])

    def test_closed_candle_selector_still_rejects_ambiguous_dual_cutoff(self):
        with self.assertRaisesRegex(ValueError, "decision_epoch or active_candle_time"):
            closed_candles(candles(2), decision_epoch=120, active_candle_time=120)

    def test_feature_builder_runtime_cutoff_excludes_boundary_and_future_candles(self):
        frames = FeatureBuilder().build(
            candles(42),
            decision_epoch=2_400,
            active_candle_time=2_400,
        )

        self.assertEqual(len(frames), 40)
        self.assertEqual(frames[-1].epoch, 2_340)

    def test_indicator_prefix_is_unchanged_by_later_closed_candles(self):
        prefix = candles(40)
        extended = [*prefix, *candles(45)[40:]]

        before = IndicatorEngine().calculate(prefix)
        after = IndicatorEngine().calculate(extended)

        self.assertEqual(before, after[:len(prefix)])

    def test_rejects_duplicate_unaligned_and_reverse_m1_epochs(self):
        cases = [
            [*candles(2), {**candles(1)[0], "time": 60}],
            [{**candles(1)[0], "time": 30}],
            [candles(2)[1], candles(2)[0]],
        ]

        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                IndicatorEngine().calculate(invalid)

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

    def test_feature_builder_accepts_the_same_explicit_causal_cutoff_contract(self):
        ambiguous = [{key: value for key, value in candle.items() if key != "is_closed"} for candle in candles(3)]

        frames = FeatureBuilder().build(ambiguous, decision_epoch=120)

        self.assertEqual([frame.epoch for frame in frames], [0, 60])


if __name__ == "__main__":
    unittest.main()
