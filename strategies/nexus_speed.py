from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from strategies.base import BaseStrategy, MoneyManager, Signal
from utils.indicator_quotes import candles_to_quotes, closed_candles


@dataclass(frozen=True)
class IndicatorSnapshot:
    ema_reference: float
    ema_previous: float
    adx: float
    atr: float


IndicatorProvider = Callable[[list[dict]], Optional[IndicatorSnapshot]]


def calculate_indicator_snapshot(candles: list[dict]) -> Optional[IndicatorSnapshot]:
    from stock_indicators import indicators

    quotes = candles_to_quotes(candles)
    ema = indicators.get_ema(quotes, 5)
    adx = indicators.get_adx(quotes, 10)
    atr = indicators.get_atr(quotes, 14)
    if len(ema) < 2 or not adx or not atr:
        return None
    values = (ema[-1].ema, ema[-2].ema, adx[-1].adx, atr[-1].atr)
    if any(value is None for value in values):
        return None
    return IndicatorSnapshot(*(float(value) for value in values))


class NexusSpeedStrategy(BaseStrategy):
    """EMA pullback strategy with a deterministic one-shot M1 state machine."""

    TERMINAL_STATES = {
        "INELIGIBLE_WARMUP",
        "INELIGIBLE_FILTER",
        "SIGNAL_EMITTED",
        "ABORTED",
    }

    def __init__(
        self,
        money_manager: Optional[MoneyManager] = None,
        *,
        duration: int = 10,
        touch_tolerance_bps: float = 1.0,
        ema_flat_tolerance_pips: float = 1.0,
        min_profit_ratio: float = 0.87,
        max_entry_delay_ticks: int = 1,
        min_closed_candles: int = 270,
        indicator_provider: IndicatorProvider = calculate_indicator_snapshot,
    ):
        super().__init__(money_manager)
        if int(duration) != 10:
            raise ValueError("Nexus Speed usa expiracao fixa de 10 segundos")
        self.duration = int(duration)
        self.duration_unit = "s"
        self.ema_period = 5
        self.adx_period = 10
        self.adx_threshold = 30.0
        self.atr_period = 14
        self.min_distance_atr = 0.30
        self.touch_tolerance_bps = float(touch_tolerance_bps)
        self.ema_flat_tolerance_pips = float(ema_flat_tolerance_pips)
        self.min_profit_ratio = float(min_profit_ratio)
        self.max_entry_delay_ticks = int(max_entry_delay_ticks)
        self.min_closed_candles = int(min_closed_candles)
        self._indicator_provider = indicator_provider

        self.state = "IDLE"
        self.state_reason = "not_started"
        self.current_candle_time: Optional[int] = None
        self.last_processed_sequence = 0
        self._previous_live_tick: Optional[dict] = None
        self._touch_tick: Optional[dict] = None
        self._snapshot: Optional[IndicatorSnapshot] = None
        self._direction: Optional[str] = None
        self._lower_band: Optional[float] = None
        self._upper_band: Optional[float] = None

    def name(self) -> str:
        return "NexusSpeed(EMA5,ADX10,ATR14)"

    def get_contract_params(self) -> dict:
        return {"duration": self.duration, "duration_unit": self.duration_unit}

    def analyze(
        self,
        ticks: list[dict],
        candles: Optional[list[dict]] = None,
    ) -> Optional[Signal]:
        candles = candles or []
        unseen = sorted(
            (
                tick
                for tick in ticks
                if tick.get("is_live") is True
                and tick.get("sequence") is not None
                and int(tick["sequence"]) > self.last_processed_sequence
            ),
            key=lambda tick: int(tick["sequence"]),
        )
        for tick in unseen:
            sequence = int(tick["sequence"])
            candle_time = int(tick["epoch"]) // 60 * 60
            has_gap = (
                self.last_processed_sequence > 0
                and sequence != self.last_processed_sequence + 1
            )
            if has_gap and self.state == "AWAITING_CONFIRMATION":
                self._abort("tick_sequence_gap")

            if self.current_candle_time is None:
                self.current_candle_time = candle_time
                self.state = "ABORTED"
                self.state_reason = "startup_mid_candle"
                self._previous_live_tick = tick
                self.last_processed_sequence = sequence
                continue

            if candle_time != self.current_candle_time:
                self._begin_candle(candle_time, candles, tick)

            signal = self._process_tick(tick)
            self._previous_live_tick = tick
            self.last_processed_sequence = sequence
            if signal is not None:
                return signal
        return None

    def _begin_candle(self, candle_time: int, candles: list[dict], tick: dict) -> None:
        self.current_candle_time = int(candle_time)
        self._previous_live_tick = None
        self._touch_tick = None
        self._snapshot = None
        self._direction = None
        self._lower_band = None
        self._upper_band = None

        closed = closed_candles(candles, self.current_candle_time)
        if len(closed) < self.min_closed_candles:
            self.state = "INELIGIBLE_WARMUP"
            self.state_reason = "warmup_incomplete"
            return
        snapshot = self._indicator_provider(closed)
        if snapshot is None:
            self.state = "INELIGIBLE_WARMUP"
            self.state_reason = "indicator_unavailable"
            return
        self._snapshot = snapshot

        active = next(
            (
                candle
                for candle in reversed(candles)
                if int(candle["time"]) == self.current_candle_time
            ),
            None,
        )
        opening = float(active["open"] if active else tick["quote"])
        pip_size = int(tick.get("pip_size") if tick.get("pip_size") is not None else 2)
        pip = 10 ** (-pip_size)
        flat_tolerance = self.ema_flat_tolerance_pips * pip
        slope = snapshot.ema_reference - snapshot.ema_previous
        epsilon = 1e-12

        if opening > snapshot.ema_reference:
            direction = "CALL"
            if slope < -flat_tolerance - epsilon:
                self._ineligible("ema_slope_against_setup")
                return
        elif opening < snapshot.ema_reference:
            direction = "PUT"
            if slope > flat_tolerance + epsilon:
                self._ineligible("ema_slope_against_setup")
                return
        else:
            self._ineligible("opening_on_ema")
            return

        if snapshot.adx <= self.adx_threshold:
            self._ineligible("adx_below_threshold")
            return
        if snapshot.atr <= 0:
            self._ineligible("atr_invalid")
            return
        required_distance = self.min_distance_atr * snapshot.atr
        if abs(opening - snapshot.ema_reference) + epsilon < required_distance:
            self._ineligible("opening_too_close")
            return

        touch_tolerance = max(
            abs(snapshot.ema_reference) * self.touch_tolerance_bps / 10_000,
            pip,
        )
        self._lower_band = snapshot.ema_reference - touch_tolerance
        self._upper_band = snapshot.ema_reference + touch_tolerance
        self._direction = direction
        self.state = f"ARMED_{direction}"
        self.state_reason = "filters_passed"

    def _process_tick(self, tick: dict) -> Optional[Signal]:
        if self.state in self.TERMINAL_STATES or self.state == "IDLE":
            return None
        if self.state == "AWAITING_CONFIRMATION":
            return self._confirm(tick)
        if self.state not in {"ARMED_CALL", "ARMED_PUT"}:
            return None
        if self._previous_live_tick is None:
            return None

        previous = float(self._previous_live_tick["quote"])
        current = float(tick["quote"])
        segment_low = min(previous, current)
        segment_high = max(previous, current)
        if segment_low <= self._upper_band and segment_high >= self._lower_band:
            self._touch_tick = dict(tick)
            self.state = "AWAITING_CONFIRMATION"
            self.state_reason = "touch_detected"
        return None

    def _confirm(self, tick: dict) -> Optional[Signal]:
        touch_price = float(self._touch_tick["quote"])
        confirmation_price = float(tick["quote"])
        direction = self._direction
        if confirmation_price == touch_price:
            self._abort("confirmation_flat")
            return None

        passed = (
            direction == "CALL"
            and confirmation_price > touch_price
            and confirmation_price >= self._lower_band
        ) or (
            direction == "PUT"
            and confirmation_price < touch_price
            and confirmation_price <= self._upper_band
        )
        if not passed:
            self._abort("confirmation_failed")
            return None

        self.state = "SIGNAL_EMITTED"
        self.state_reason = "confirmation_passed"
        return Signal(
            action=direction,
            reason=f"Nexus Speed {direction}: toque EMA confirmado no tick seguinte",
            price=confirmation_price,
            timestamp=int(tick["epoch"]),
            tick_sequence=int(tick["sequence"]),
            candle_time=self.current_candle_time,
        )

    def _ineligible(self, reason: str) -> None:
        self.state = "INELIGIBLE_FILTER"
        self.state_reason = reason

    def _abort(self, reason: str) -> None:
        self.state = "ABORTED"
        self.state_reason = reason
