import unittest

try:
    from strategies.nexus_speed import IndicatorSnapshot, NexusSpeedStrategy
except ModuleNotFoundError:
    IndicatorSnapshot = None
    NexusSpeedStrategy = None


class NexusSpeedStrategyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(NexusSpeedStrategy, "strategies.nexus_speed must exist")

    @staticmethod
    def _snapshot(ema=100.0, previous=99.0, adx=31.0, atr=20.0):
        return IndicatorSnapshot(
            ema_reference=ema,
            ema_previous=previous,
            adx=adx,
            atr=atr,
        )

    @staticmethod
    def _candles(active_time, opening):
        candles = [
            {"time": 0, "open": 97, "high": 99, "low": 96, "close": 98},
            {"time": 60, "open": 98, "high": 100, "low": 97, "close": 99},
            {"time": 120, "open": 99, "high": 101, "low": 98, "close": 100},
        ]
        if active_time >= 180:
            candles.append({
                "time": 180,
                "open": opening,
                "high": opening,
                "low": opening,
                "close": opening,
            })
        if active_time >= 240:
            candles.append({
                "time": 240,
                "open": opening,
                "high": opening,
                "low": opening,
                "close": opening,
            })
        return candles

    @staticmethod
    def _tick(sequence, epoch, quote, pip_size=2):
        return {
            "sequence": sequence,
            "epoch": epoch,
            "quote": quote,
            "symbol": "R_100",
            "pip_size": pip_size,
            "is_live": True,
        }

    def _armed_strategy(self, snapshot, opening, duration=5, adx_threshold=30):
        strategy = NexusSpeedStrategy(
            duration=duration,
            adx_threshold=adx_threshold,
            min_closed_candles=3,
            indicator_provider=lambda _: snapshot,
        )
        startup_tick = self._tick(1, 121, 100.0)
        strategy.analyze([startup_tick], self._candles(120, opening))
        opening_tick = self._tick(2, 181, opening)
        ticks = [startup_tick, opening_tick]
        strategy.analyze(ticks, self._candles(180, opening))
        return strategy, ticks

    def test_starting_mid_candle_waits_for_the_next_candle(self):
        strategy = NexusSpeedStrategy(
            min_closed_candles=3,
            indicator_provider=lambda _: self._snapshot(),
        )

        signal = strategy.analyze(
            [self._tick(1, 121, 110.0)],
            self._candles(120, 110.0),
        )

        self.assertIsNone(signal)
        self.assertEqual(strategy.state, "ABORTED")
        self.assertEqual(strategy.state_reason, "startup_mid_candle")

    def test_adx_must_be_strictly_greater_than_thirty(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(adx=30.0),
            opening=110.0,
        )

        self.assertEqual(strategy.state, "INELIGIBLE_FILTER")
        self.assertEqual(strategy.state_reason, "adx_below_threshold")

    def test_custom_adx_threshold_is_strict(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(adx=25.0), opening=110.0, adx_threshold=25
        )

        self.assertEqual(strategy.state, "INELIGIBLE_FILTER")
        self.assertEqual(strategy.state_reason, "adx_below_threshold")

    def test_custom_adx_threshold_accepts_higher_adx(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(adx=25.1), opening=110.0, adx_threshold=25
        )

        self.assertEqual(strategy.state, "ARMED_CALL")

    def test_adx_threshold_accepts_only_approved_values(self):
        for threshold in (20, 25, 30):
            with self.subTest(threshold=threshold):
                strategy = NexusSpeedStrategy(adx_threshold=threshold)
                self.assertEqual(strategy.adx_threshold, float(threshold))

        with self.assertRaisesRegex(ValueError, "20, 25 ou 30"):
            NexusSpeedStrategy(adx_threshold=21)

    def test_distance_equal_to_thirty_percent_atr_is_eligible(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=99.0, atr=20.0),
            opening=106.0,
        )

        self.assertEqual(strategy.state, "ARMED_CALL")

    def test_expiration_is_fixed_at_five_ticks(self):
        strategy, _ = self._armed_strategy(self._snapshot(), opening=110.0)

        self.assertEqual(strategy.get_contract_params(), {
            "duration": 5,
            "duration_unit": "t",
        })
        with self.assertRaises(ValueError):
            NexusSpeedStrategy(duration=10, indicator_provider=lambda _: self._snapshot())

    def test_put_accepts_descending_or_one_pip_flat_ema(self):
        descending, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=101.0),
            opening=90.0,
        )
        flat, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=99.99),
            opening=90.0,
        )

        self.assertEqual(descending.state, "ARMED_PUT")
        self.assertEqual(flat.state, "ARMED_PUT")

    def test_put_rejects_ema_rising_more_than_one_pip(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=99.98),
            opening=90.0,
        )

        self.assertEqual(strategy.state, "INELIGIBLE_FILTER")
        self.assertEqual(strategy.state_reason, "ema_slope_against_setup")

    def test_call_accepts_rising_or_one_pip_flat_ema(self):
        rising, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=99.0),
            opening=110.0,
        )
        flat, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=100.01),
            opening=110.0,
        )

        self.assertEqual(rising.state, "ARMED_CALL")
        self.assertEqual(flat.state, "ARMED_CALL")

    def test_call_rejects_ema_falling_more_than_one_pip(self):
        strategy, _ = self._armed_strategy(
            self._snapshot(ema=100.0, previous=100.02),
            opening=110.0,
        )

        self.assertEqual(strategy.state, "INELIGIBLE_FILTER")
        self.assertEqual(strategy.state_reason, "ema_slope_against_setup")

    def test_call_crossing_then_next_tick_rejection_emits_signal(self):
        strategy, ticks = self._armed_strategy(self._snapshot(), opening=110.0)
        ticks.extend([
            self._tick(3, 190, 99.995),
            self._tick(4, 191, 100.02),
        ])

        signal = strategy.analyze(ticks, self._candles(180, 110.0))

        self.assertEqual(signal.action, "CALL")
        self.assertEqual(signal.tick_sequence, 4)
        self.assertEqual(signal.candle_time, 180)
        self.assertEqual(strategy.state, "SIGNAL_EMITTED")
        self.assertEqual(strategy.get_contract_params(), {
            "duration": 5,
            "duration_unit": "t",
        })

    def test_put_crossing_then_next_tick_rejection_emits_signal(self):
        strategy, ticks = self._armed_strategy(
            self._snapshot(ema=100.0, previous=101.0),
            opening=90.0,
        )
        ticks.extend([
            self._tick(3, 190, 100.005),
            self._tick(4, 191, 99.98),
        ])

        signal = strategy.analyze(ticks, self._candles(180, 90.0))

        self.assertEqual(signal.action, "PUT")
        self.assertEqual(signal.tick_sequence, 4)
        self.assertEqual(strategy.get_contract_params(), {
            "duration": 5,
            "duration_unit": "t",
        })

    def test_flat_confirmation_aborts_the_candle(self):
        strategy, ticks = self._armed_strategy(self._snapshot(), opening=110.0)
        ticks.extend([
            self._tick(3, 190, 100.0),
            self._tick(4, 191, 100.0),
        ])

        signal = strategy.analyze(ticks, self._candles(180, 110.0))

        self.assertIsNone(signal)
        self.assertEqual(strategy.state, "ABORTED")
        self.assertEqual(strategy.state_reason, "confirmation_flat")

    def test_sequence_gap_after_touch_aborts_the_candle(self):
        strategy, ticks = self._armed_strategy(self._snapshot(), opening=110.0)
        ticks.append(self._tick(3, 190, 100.0))
        strategy.analyze(ticks, self._candles(180, 110.0))
        ticks.append(self._tick(5, 191, 100.02))

        signal = strategy.analyze(ticks, self._candles(180, 110.0))

        self.assertIsNone(signal)
        self.assertEqual(strategy.state, "ABORTED")
        self.assertEqual(strategy.state_reason, "tick_sequence_gap")

    def test_same_ticks_never_emit_duplicate_signal(self):
        strategy, ticks = self._armed_strategy(self._snapshot(), opening=110.0)
        ticks.extend([
            self._tick(3, 190, 100.0),
            self._tick(4, 191, 100.02),
        ])
        first = strategy.analyze(ticks, self._candles(180, 110.0))

        duplicate = strategy.analyze(ticks, self._candles(180, 110.0))

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)


if __name__ == "__main__":
    unittest.main()
