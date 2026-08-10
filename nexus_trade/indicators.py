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


def closed_candles(
    candles: Iterable[dict],
    *,
    decision_epoch: int | None = None,
    active_candle_time: int | None = None,
) -> list[dict]:
    """Select an unambiguous M1-closed causal prefix.

    A candle needs an explicit closed marker, or a supplied cutoff proving its
    opening bucket is strictly earlier than the active/decision bucket.  Gaps are
    valid market gaps; duplicate, reverse, and non-M1 buckets are not.
    """
    if decision_epoch is not None and active_candle_time is not None:
        raise ValueError("provide decision_epoch or active_candle_time, not both")
    cutoff = decision_epoch if decision_epoch is not None else active_candle_time
    if cutoff is not None and (isinstance(cutoff, bool) or type(cutoff) is not int):
        raise ValueError("causal cutoff must be an integer epoch")
    if active_candle_time is not None and active_candle_time % 60:
        raise ValueError("active_candle_time must align to M1")

    result: list[dict] = []
    previous: int | None = None
    for candle in candles:
        normalized = dict(candle)
        raw_epoch = normalized.get("time", normalized.get("open_epoch"))
        if isinstance(raw_epoch, bool) or type(raw_epoch) is not int or raw_epoch % 60:
            raise ValueError("candle time must be a unique, M1-aligned integer epoch")
        if previous is not None and raw_epoch <= previous:
            raise ValueError("candle epochs must be strictly increasing")
        previous = raw_epoch
        normalized["time"] = raw_epoch
        explicit_live = (
            normalized.get("is_closed") is False
            or normalized.get("closed") is False
            or ("close_epoch" in normalized and normalized["close_epoch"] is None)
        )
        explicit_closed = (
            normalized.get("is_closed") is True
            or normalized.get("closed") is True
            or ("close_epoch" in normalized and normalized["close_epoch"] is not None)
        )
        if explicit_live and explicit_closed:
            raise ValueError("candle closure markers conflict")
        if explicit_live:
            continue
        if explicit_closed:
            result.append(normalized)
        elif cutoff is None:
            raise ValueError("candle closure is ambiguous without a causal cutoff")
        elif raw_epoch < cutoff:
            result.append(normalized)
    return result


def number(value: object) -> float | None:
    return None if value is None else float(value)


class IndicatorEngine:
    """Compute the V1 Bollinger/ADX frame from the closed-candle prefix only."""

    def calculate(
        self,
        candles: Iterable[dict],
        *,
        decision_epoch: int | None = None,
        active_candle_time: int | None = None,
    ) -> list[IndicatorFrame]:
        completed = closed_candles(
            candles, decision_epoch=decision_epoch, active_candle_time=active_candle_time,
        )
        if not completed:
            return []
        quotes = candles_to_quotes(completed)
        bollinger = indicators.get_bollinger_bands(quotes, 20, 2)
        adx = indicators.get_adx(quotes, 14)
        if len(bollinger) != len(completed) or len(adx) != len(completed):
            raise ValueError("indicator library returned an incomplete causal series")
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
