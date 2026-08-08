from datetime import datetime, timezone
from typing import Iterable

from stock_indicators.indicators.common.quote import Quote


def closed_candles(candles: Iterable[dict], active_candle_time: int) -> list[dict]:
    """Return only candles whose UTC bucket precedes the active M1 candle."""
    return [
        candle
        for candle in candles
        if int(candle["time"]) < int(active_candle_time)
    ]


def candles_to_quotes(candles: Iterable[dict]) -> list[Quote]:
    """Convert normalized Deriv OHLC candles to Stock Indicators quotes."""
    return [
        Quote(
            datetime.fromtimestamp(
                int(candle["time"]), tz=timezone.utc
            ).replace(tzinfo=None),
            float(candle["open"]),
            float(candle["high"]),
            float(candle["low"]),
            float(candle["close"]),
            0.0,
        )
        for candle in candles
    ]
