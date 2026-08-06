import unittest

from data.candles import CandleAggregator


class CandleAggregatorTests(unittest.TestCase):
    def test_ticks_update_the_same_minute_candle(self):
        aggregator = CandleAggregator(60)
        aggregator.update(120, 10.0)
        aggregator.update(135, 8.0)
        candle = aggregator.update(150, 13.0)

        self.assertEqual(candle.as_dict(), {
            "time": 120,
            "open": 10.0,
            "high": 13.0,
            "low": 8.0,
            "close": 13.0,
        })

    def test_tick_in_next_bucket_starts_a_new_candle(self):
        aggregator = CandleAggregator(60)
        aggregator.update(179, 10.0)
        candle = aggregator.update(180, 11.0)

        self.assertEqual(candle.as_dict(), {
            "time": 180,
            "open": 11.0,
            "high": 11.0,
            "low": 11.0,
            "close": 11.0,
        })

    def test_late_tick_does_not_rewrite_a_closed_bucket(self):
        aggregator = CandleAggregator(60)
        aggregator.update(120, 10.0)
        current = aggregator.update(180, 11.0)
        late_result = aggregator.update(150, 99.0)

        self.assertIs(late_result, current)
        self.assertEqual(current.high, 11.0)


if __name__ == "__main__":
    unittest.main()
