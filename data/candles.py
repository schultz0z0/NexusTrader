from dataclasses import dataclass


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float

    def as_dict(self):
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }


class CandleAggregator:
    def __init__(self, timeframe_seconds: int):
        if timeframe_seconds <= 0:
            raise ValueError("timeframe_seconds deve ser positivo")
        self.timeframe_seconds = int(timeframe_seconds)
        self.current = None

    def seed(self, candle) -> Candle:
        """Continue the latest historical bucket without losing its OHLC."""
        source = candle.as_dict() if isinstance(candle, Candle) else candle
        bucket = int(source["time"]) - (int(source["time"]) % self.timeframe_seconds)
        self.current = Candle(
            time=bucket,
            open=float(source["open"]),
            high=float(source["high"]),
            low=float(source["low"]),
            close=float(source["close"]),
        )
        return self.current

    def update(self, epoch: int, price: float) -> Candle:
        bucket = int(epoch) - (int(epoch) % self.timeframe_seconds)
        numeric_price = float(price)
        if self.current is None or bucket > self.current.time:
            self.current = Candle(
                time=bucket,
                open=numeric_price,
                high=numeric_price,
                low=numeric_price,
                close=numeric_price,
            )
        elif bucket == self.current.time:
            self.current.high = max(self.current.high, numeric_price)
            self.current.low = min(self.current.low, numeric_price)
            self.current.close = numeric_price
        return self.current
