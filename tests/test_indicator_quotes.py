import importlib
import unittest
from datetime import datetime, timezone


class IndicatorQuoteAdapterTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module("utils.indicator_quotes")
        except ModuleNotFoundError:
            self.module = None

    def test_active_candle_is_excluded_from_indicator_input(self):
        self.assertIsNotNone(self.module, "utils.indicator_quotes must exist")
        candles = [
            {"time": 60, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"time": 120, "open": 1.5, "high": 3, "low": 1, "close": 2.5},
        ]

        result = self.module.closed_candles(candles, active_candle_time=120)

        self.assertEqual(result, [candles[0]])

    def test_candles_become_utc_quotes_with_zero_volume(self):
        self.assertIsNotNone(self.module, "utils.indicator_quotes must exist")
        candle = {
            "time": 1_700_000_040,
            "open": 100.1,
            "high": 101.2,
            "low": 99.8,
            "close": 100.7,
        }

        quote = self.module.candles_to_quotes([candle])[0]

        expected_utc = datetime.fromtimestamp(candle["time"], tz=timezone.utc).replace(
            tzinfo=None
        )
        self.assertEqual(quote.date, expected_utc)
        self.assertEqual(float(quote.open), 100.1)
        self.assertEqual(float(quote.high), 101.2)
        self.assertEqual(float(quote.low), 99.8)
        self.assertEqual(float(quote.close), 100.7)
        self.assertEqual(float(quote.volume), 0.0)


if __name__ == "__main__":
    unittest.main()
