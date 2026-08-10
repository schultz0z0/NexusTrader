"""Causal indicator calculation for closed NexusTrade candles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from stock_indicators import indicators

from utils.indicator_quotes import candles_to_quotes


@dataclass(frozen=True)
class IndicatorFrame:
    epoch: int
    upper: float | None
    middle: float | None
    lower: float | None
    adx: float | None
    values: dict[str, float | None]


def closed_candles(candles: Iterable[dict]) -> list[dict]:
    """Return only candles explicitly marked closed (or legacy complete candles)."""
    result = []
    for candle in candles:
        if candle.get("is_closed") is False or candle.get("closed") is False:
            continue
        if "close_epoch" in candle and candle["close_epoch"] is None:
            continue
        normalized = dict(candle)
        if "time" not in normalized:
            normalized["time"] = normalized["open_epoch"]
        result.append(normalized)
    return result


def number(value: object) -> float | None:
    return None if value is None else float(value)


class IndicatorEngine:
    """Compute the V1 Bollinger/ADX frame from the closed-candle prefix only."""

    def calculate(self, candles: Iterable[dict]) -> list[IndicatorFrame]:
        completed = closed_candles(candles)
        if not completed:
            return []
        quotes = candles_to_quotes(completed)
        bollinger = indicators.get_bollinger_bands(quotes, 20, 2)
        adx = indicators.get_adx(quotes, 14)
        frames: list[IndicatorFrame] = []
        prior_middle: float | None = None
        for candle, bands, trend in zip(completed, bollinger, adx):
            upper = number(bands.upper_band)
            middle = number(bands.sma)
            lower = number(bands.lower_band)
            values = {
                "bollinger_percent_b": number(bands.percent_b),
                "bollinger_z_score": number(bands.z_score),
                "bollinger_width": number(bands.width),
                "bollinger_slope": None if middle is None or prior_middle is None else middle - prior_middle,
                "adx_pdi": number(trend.pdi),
                "adx_mdi": number(trend.mdi),
            }
            frames.append(IndicatorFrame(
                epoch=int(candle["time"]), upper=upper, middle=middle, lower=lower,
                adx=number(trend.adx), values=values,
            ))
            prior_middle = middle
        return frames
