"""Feature repertoire built only from candles already closed at decision time."""

from __future__ import annotations

from typing import Iterable

from stock_indicators import indicators

from nexus_trade.indicators import (
    IndicatorEngine,
    IndicatorFrame,
    closed_candles,
    exclusive_causal_cutoff,
    number,
)
from utils.indicator_quotes import candles_to_quotes


class FeatureBuilder:
    """Enrich causal indicator frames without filling any warmup values."""

    def __init__(self, indicator_engine: IndicatorEngine | None = None):
        self.indicator_engine = indicator_engine or IndicatorEngine()

    def build(
        self,
        candles: Iterable[dict],
        *,
        decision_epoch: int | None = None,
        active_candle_time: int | None = None,
    ) -> list[IndicatorFrame]:
        source = list(candles)
        cutoff = exclusive_causal_cutoff(
            decision_epoch=decision_epoch,
            active_candle_time=active_candle_time,
        )
        completed = closed_candles(
            source,
            **cutoff,
        )
        base_frames = self.indicator_engine.calculate(
            source,
            **cutoff,
        )
        if not completed:
            return []
        quotes = candles_to_quotes(completed)
        series = {
            "chop": indicators.get_chop(quotes, 14),
            "atr": indicators.get_atr(quotes, 14),
            "rsi": indicators.get_rsi(quotes, 14),
            "stoch": indicators.get_stoch(quotes, 14, 3, 3),
            "cci": indicators.get_cci(quotes, 20),
            "keltner": indicators.get_keltner(quotes, 20, 2, 10),
            "roc": indicators.get_roc(quotes, 12),
            "aroon": indicators.get_aroon(quotes, 25),
            "sma": indicators.get_sma(quotes, 20),
            "ema": indicators.get_ema(quotes, 20),
            "wma": indicators.get_wma(quotes, 20),
            "hma": indicators.get_hma(quotes, 20),
            "kama": indicators.get_kama(quotes, 10, 2, 30),
        }
        frames: list[IndicatorFrame] = []
        for index, (candle, frame) in enumerate(zip(completed, base_frames)):
            values = dict(frame.values)
            values.update(self._indicator_values(series, index))
            values.update(self._candle_shape(candle))
            frames.append(IndicatorFrame(
                epoch=frame.epoch, upper=frame.upper, middle=frame.middle,
                lower=frame.lower, adx=frame.adx, values=values,
            ))
        return frames

    @staticmethod
    def _indicator_values(series: dict[str, list], index: int) -> dict[str, float | None]:
        atr = series["atr"][index]
        stoch = series["stoch"][index]
        keltner = series["keltner"][index]
        roc = series["roc"][index]
        aroon = series["aroon"][index]
        return {
            "chop": number(series["chop"][index].chop),
            "atr": number(atr.atr),
            "atrp": number(atr.atrp),
            "rsi": number(series["rsi"][index].rsi),
            "stoch_k": number(stoch.k),
            "stoch_d": number(stoch.d),
            "cci": number(series["cci"][index].cci),
            "keltner_upper": number(keltner.upper_band),
            "keltner_center": number(keltner.center_line),
            "keltner_lower": number(keltner.lower_band),
            "roc": number(roc.roc),
            "aroon_up": number(aroon.aroon_up),
            "aroon_down": number(aroon.aroon_down),
            "sma": number(series["sma"][index].sma),
            "ema": number(series["ema"][index].ema),
            "wma": number(series["wma"][index].wma),
            "hma": number(series["hma"][index].hma),
            "kama": number(series["kama"][index].kama),
        }

    @staticmethod
    def _candle_shape(candle: dict) -> dict[str, float | None]:
        opening = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        full_range = high - low
        body = close - opening
        upper_wick = high - max(opening, close)
        lower_wick = min(opening, close) - low
        return {
            "body": body,
            "body_ratio": None if full_range == 0 else body / full_range,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "upper_wick_ratio": None if full_range == 0 else upper_wick / full_range,
            "lower_wick_ratio": None if full_range == 0 else lower_wick / full_range,
        }
