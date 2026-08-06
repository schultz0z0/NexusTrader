from collections import deque
from statistics import mean, stdev

from config.settings import settings
from core.event_publisher import HttpEventPublisher
from core.events import runtime_event
from data.candles import CandleAggregator
from utils.logger import setup_logger

logger = setup_logger("MarketData")


class MarketDataHandler:
    """Loads Deriv history and publishes a normalized live market stream."""

    def __init__(
        self,
        connection,
        bot_id="default",
        publisher=None,
        buffer_size=500,
        bollinger_period=20,
        bollinger_std_dev=2.0,
        history_resync_seconds=None,
    ):
        self.connection = connection
        self.bot_id = bot_id
        self.publisher = publisher or HttpEventPublisher()
        self._owns_publisher = publisher is None
        self.buffer_size = int(buffer_size)
        self.bollinger_period = int(bollinger_period)
        try:
            self.bollinger_std_dev = float(bollinger_std_dev)
        except (TypeError, ValueError):
            self.bollinger_std_dev = None
        self._ticks = deque(maxlen=self.buffer_size)
        self._candles = deque(maxlen=self.buffer_size)
        self._subscription_key = None
        self.symbol = None
        self.timeframe_seconds = 60
        self._aggregator = CandleAggregator(60)
        self.history_resync_seconds = int(
            history_resync_seconds
            if history_resync_seconds is not None
            else settings.MARKET_HISTORY_RESYNC_SECONDS
        )
        self._last_history_publish_epoch = 0

    async def start(self, symbol: str, timeframe_seconds: int = 60):
        self.symbol = symbol
        self.timeframe_seconds = max(1, int(timeframe_seconds))
        self._aggregator = CandleAggregator(self.timeframe_seconds)
        await self.publisher.start()
        await self._load_and_publish_history()
        await self._subscribe_live_ticks()

    def _calculate_history_indicators(self, points: list):
        if self.bollinger_std_dev is not None or not points:
            return {}, []
            
        upper_hist, middle_hist, lower_hist = [], [], []
        
        # Donchian Channel History
        for i in range(len(points)):
            window = points[max(0, i - self.bollinger_period + 1):i + 1]
            if len(window) < self.bollinger_period:
                continue
            u = max(c.get("high", c.get("value", c.get("close", 0))) for c in window)
            l = min(c.get("low", c.get("value", c.get("close", 0))) for c in window)
            m = (u + l) / 2
            t = points[i]["time"]
            upper_hist.append({"time": t, "value": u})
            middle_hist.append({"time": t, "value": m})
            lower_hist.append({"time": t, "value": l})
            
        donchian = {"upper": upper_hist, "middle": middle_hist, "lower": lower_hist}
        
        from utils.indicators import calculate_zigzag
        zigzag = calculate_zigzag(points, depth=15, deviation=0.0, backstep=3)

        return donchian, zigzag

    async def _load_and_publish_history(self):
        line_mode = self.timeframe_seconds <= 1
        request = {
            "ticks_history": self.symbol,
            "end": "latest",
            "count": 500,
            "style": "ticks" if line_mode else "candles",
        }
        if not line_mode:
            request["granularity"] = self.timeframe_seconds
        response = await self.connection.send(request)
        if "error" in response:
            logger.warning(f"Historico indisponivel para {self.symbol}: {response['error']}")
            points = []
        elif line_mode:
            history = response.get("history", {})
            prices = history.get("prices", [])
            times = history.get("times", [])
            points = [
                {"time": int(epoch), "value": float(price)}
                for epoch, price in zip(times, prices)
            ]
            for point in points:
                self._ticks.append({
                    "quote": point["value"],
                    "epoch": point["time"],
                    "symbol": self.symbol,
                })
        else:
            points = [
                {
                    "time": int(item.get("epoch", item.get("time"))),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                }
                for item in response.get("candles", [])
            ]
            for point in points:
                self._ticks.append({
                    "quote": point["close"],
                    "epoch": point["time"],
                    "symbol": self.symbol,
                })
                self._candles.append(point)
            if points:
                self._aggregator.seed(points[-1])

        await self._publish_history(points, line_mode)

    async def _publish_history(self, points, line_mode, epoch=None):
        donchian, zigzag = self._calculate_history_indicators(points)
        await self.publisher.publish(runtime_event(
            "market.history",
            self.bot_id,
            symbol=self.symbol,
            timeframe_seconds=self.timeframe_seconds,
            mode="line" if line_mode else "candles",
            points=points,
            donchian=donchian,
            zigzag=zigzag,
        ))
        if epoch is not None:
            self._last_history_publish_epoch = int(epoch)
        elif points:
            self._last_history_publish_epoch = int(points[-1]["time"])

    async def _subscribe_live_ticks(self):
        self._subscription_key = f"ticks:{self.bot_id}:{self.symbol}"

        async def on_tick(message):
            tick = message.get("tick")
            if not tick:
                return
            epoch = int(tick["epoch"])
            price = float(tick["quote"])
            symbol = tick.get("symbol", self.symbol)
            self._ticks.append({"quote": price, "epoch": epoch, "symbol": symbol})
            candle = self._aggregator.update(epoch, price)
            
            if len(self._candles) > 0 and self._candles[-1]["time"] == candle.time:
                self._candles[-1] = candle.as_dict()
            else:
                self._candles.append(candle.as_dict())
                
            await self.publisher.publish(runtime_event(
                "market.tick",
                self.bot_id,
                epoch=epoch,
                symbol=symbol,
                timeframe_seconds=self.timeframe_seconds,
                price=price,
                candle=candle.as_dict(),
                bollinger=self._bollinger_values(),
                zigzag=self._calculate_history_indicators(list(self._candles))[1],
            ))
            if epoch - self._last_history_publish_epoch >= self.history_resync_seconds:
                if self.timeframe_seconds <= 1:
                    points = [
                        {"time": int(item["epoch"]), "value": float(item["quote"])}
                        for item in self._ticks
                    ]
                    line_mode = True
                else:
                    points = list(self._candles)
                    line_mode = False
                await self._publish_history(points, line_mode, epoch=epoch)

        await self.connection.subscribe(
            self._subscription_key,
            {"ticks": self.symbol},
            on_tick,
        )
        logger.info(f"Feed de ticks ativo para {self.symbol} no bot {self.bot_id}.")

    def _bollinger_values(self):
        is_donchian = self.bollinger_std_dev is None

        if self.timeframe_seconds > 1:
            window_candles = list(self._candles)[-self.bollinger_period:]
            if len(window_candles) < self.bollinger_period:
                return {"upper": None, "middle": None, "lower": None}
            
            if is_donchian:
                upper = max(c.get("high", c.get("close", 0)) for c in window_candles)
                lower = min(c.get("low", c.get("close", 0)) for c in window_candles)
                middle = (upper + lower) / 2
                return {"upper": upper, "middle": middle, "lower": lower}
            else:
                closes = [c.get("close", 0) for c in window_candles]
                middle = mean(closes)
                deviation = stdev(closes) if len(closes) > 1 else 0.0
                distance = deviation * self.bollinger_std_dev
                return {"upper": middle + distance, "middle": middle, "lower": middle - distance}
        else:
            quotes = [tick["quote"] for tick in self._ticks]
            if len(quotes) < self.bollinger_period:
                return {"upper": None, "middle": None, "lower": None}
            window = quotes[-self.bollinger_period:]
            if is_donchian:
                upper = max(window)
                lower = min(window)
                middle = (upper + lower) / 2
                return {"upper": upper, "middle": middle, "lower": lower}
            else:
                middle = mean(window)
                deviation = stdev(window) if len(window) > 1 else 0.0
                distance = deviation * self.bollinger_std_dev
                return {"upper": middle + distance, "middle": middle, "lower": middle - distance}

    async def subscribe_ticks(self, symbol: str):
        await self.start(symbol, timeframe_seconds=self.timeframe_seconds)
        record = self.connection._subscriptions.get(self._subscription_key)
        return record.remote_id if record else None

    def get_latest_tick(self, symbol: str = None):
        return self._ticks[-1] if self._ticks else None

    def get_tick_history(self, symbol: str = None):
        return list(self._ticks)

    def get_candle_history(self, symbol: str = None):
        return list(self._candles)

    async def close(self):
        if self._subscription_key:
            await self.connection.unsubscribe(self._subscription_key)
        if self._owns_publisher:
            await self.publisher.close()
