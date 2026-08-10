"""Deterministic Champion V1 signal state machine for closed M1 candles."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from nexus_trade.constants import NEXUS_TIMEFRAME_SECONDS
from nexus_trade.domain import Lane
from nexus_trade.indicators import IndicatorFrame


ADX_ENTRY_MAX = 22.0


@dataclass(frozen=True)
class SetupState:
    """Primitive-only state needed to resume the causal strategy exactly."""

    upper_break_epoch: int | None = None
    lower_break_epoch: int | None = None
    previous_upper: float | None = None
    previous_lower: float | None = None
    last_candle_epoch: int | None = None
    position_active: bool = False

    def to_dict(self) -> dict[str, int | float | bool | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SetupState":
        return cls(
            upper_break_epoch=value.get("upper_break_epoch"),
            lower_break_epoch=value.get("lower_break_epoch"),
            previous_upper=value.get("previous_upper"),
            previous_lower=value.get("previous_lower"),
            last_candle_epoch=value.get("last_candle_epoch"),
            position_active=bool(value.get("position_active", False)),
        )


@dataclass(frozen=True)
class Decision:
    """Persistible result for every evaluated closed candle."""

    decision_id: str
    contract_type: str | None
    reason_codes: tuple[str, ...]
    signal_epoch: int
    target_epoch: int
    adx: float | None
    blocked_reason: str | None
    lane: str = Lane.CHAMPION.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Decision":
        return cls(
            decision_id=str(value["decision_id"]),
            contract_type=value.get("contract_type"),
            reason_codes=tuple(value.get("reason_codes", ())),
            signal_epoch=int(value["signal_epoch"]),
            target_epoch=int(value["target_epoch"]),
            adx=None if value.get("adx") is None else float(value["adx"]),
            blocked_reason=value.get("blocked_reason"),
            lane=str(value.get("lane", Lane.CHAMPION.value)),
        )


def _field(value: object, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    raise ValueError(f"missing required field: {names[0]}")


def _optional_field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _finite_number(value: object, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


class NexusTradeStrategy:
    """Evaluate Champion V1 from a strictly increasing stream of closed candles."""

    def __init__(
        self,
        *,
        state: SetupState | Mapping[str, Any] | None = None,
        lane: Lane | str = Lane.CHAMPION,
    ):
        self.state = (
            SetupState()
            if state is None
            else state
            if isinstance(state, SetupState)
            else SetupState.from_dict(state)
        )
        try:
            self.lane = Lane(lane).value
        except ValueError as exc:
            raise ValueError("lane must be champion_baseline or challenger_trial") from exc

    def snapshot(self) -> dict[str, int | float | bool | None]:
        return self.state.to_dict()

    def mark_position_active(self) -> None:
        self.state = replace(
            self.state,
            upper_break_epoch=None,
            lower_break_epoch=None,
            position_active=True,
        )

    def mark_position_closed(self) -> None:
        self.state = replace(self.state, position_active=False)

    def on_closed_candle(
        self,
        candle: object,
        indicators: IndicatorFrame | Mapping[str, Any],
    ) -> list[Decision]:
        epoch = _field(candle, "open_epoch", "time")
        if isinstance(epoch, bool) or type(epoch) is not int or epoch % NEXUS_TIMEFRAME_SECONDS:
            raise ValueError("closed candle epoch must be an M1-aligned integer")
        if _optional_field(candle, "is_closed") is False or _optional_field(candle, "closed") is False:
            raise ValueError("strategy accepts closed candles only")
        if self.state.last_candle_epoch is not None and epoch <= self.state.last_candle_epoch:
            raise ValueError("closed candle epochs must be strictly increasing")

        indicator_epoch = _field(indicators, "epoch")
        if indicator_epoch != epoch:
            raise ValueError("indicator frame must belong to the closed candle")
        opening = _finite_number(_field(candle, "open"), "open")
        close = _finite_number(_field(candle, "close"), "close")
        upper = _finite_number(_field(indicators, "upper"), "upper", optional=True)
        middle = _finite_number(_field(indicators, "middle"), "middle", optional=True)
        lower = _finite_number(_field(indicators, "lower"), "lower", optional=True)
        adx = _finite_number(_field(indicators, "adx"), "adx", optional=True)

        upper_break_epoch = self.state.upper_break_epoch
        lower_break_epoch = self.state.lower_break_epoch
        contract_type: str | None = None
        reason_codes: list[str] = []

        if upper_break_epoch is not None and close < opening:
            contract_type = "PUT"
            reason_codes.append("upper_reversal_confirmation")
            upper_break_epoch = None
        if lower_break_epoch is not None and close > opening:
            contract_type = "CALL"
            reason_codes.append("lower_reversal_confirmation")
            lower_break_epoch = None

        if middle is not None and opening < middle < close:
            contract_type = "CALL"
            reason_codes.append("center_cross_up")
        elif middle is not None and close < middle < opening:
            contract_type = "PUT"
            reason_codes.append("center_cross_down")

        upper_started = (
            not self.state.position_active
            and upper_break_epoch is None
            and upper is not None
            and self.state.previous_upper is not None
            and close > upper
            and upper > self.state.previous_upper
        )
        lower_started = (
            not self.state.position_active
            and lower_break_epoch is None
            and lower is not None
            and self.state.previous_lower is not None
            and close < lower
            and lower < self.state.previous_lower
        )
        if upper_started:
            upper_break_epoch = epoch
        if lower_started:
            lower_break_epoch = epoch

        reason_codes = list(dict.fromkeys(reason_codes))
        blocked_reason: str | None
        position_active = self.state.position_active
        if contract_type is None:
            blocked_reason = "NO_TRADE"
            audit_reasons = self._no_trade_reasons(
                upper_started=upper_started,
                lower_started=lower_started,
                upper_break_epoch=upper_break_epoch,
                lower_break_epoch=lower_break_epoch,
            )
            reason_codes.extend(code for code in audit_reasons if code not in reason_codes)
        elif position_active:
            blocked_reason = "POSITION_ACTIVE"
            upper_break_epoch = None
            lower_break_epoch = None
        elif adx is None or adx > ADX_ENTRY_MAX:
            blocked_reason = "ADX_BLOCKED"
            upper_break_epoch = None
            lower_break_epoch = None
        else:
            blocked_reason = None
            position_active = True
            upper_break_epoch = None
            lower_break_epoch = None

        self.state = SetupState(
            upper_break_epoch=upper_break_epoch,
            lower_break_epoch=lower_break_epoch,
            previous_upper=upper,
            previous_lower=lower,
            last_candle_epoch=epoch,
            position_active=position_active,
        )
        decision = self._decision(
            contract_type=contract_type,
            reason_codes=tuple(reason_codes),
            signal_epoch=epoch,
            adx=adx,
            blocked_reason=blocked_reason,
        )
        return [decision]

    @staticmethod
    def _no_trade_reasons(
        *,
        upper_started: bool,
        lower_started: bool,
        upper_break_epoch: int | None,
        lower_break_epoch: int | None,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if upper_started:
            reasons.append("upper_break_detected")
        if lower_started:
            reasons.append("lower_break_detected")
        if upper_break_epoch is not None and not upper_started:
            reasons.append("waiting_bearish_confirmation")
        if lower_break_epoch is not None and not lower_started:
            reasons.append("waiting_bullish_confirmation")
        return tuple(reasons or ("no_trade",))

    def _decision(
        self,
        *,
        contract_type: str | None,
        reason_codes: tuple[str, ...],
        signal_epoch: int,
        adx: float | None,
        blocked_reason: str | None,
    ) -> Decision:
        target_epoch = signal_epoch + NEXUS_TIMEFRAME_SECONDS
        identity = {
            "lane": self.lane,
            "contract_type": contract_type,
            "reason_codes": reason_codes,
            "signal_epoch": signal_epoch,
            "target_epoch": target_epoch,
            "adx": adx,
            "blocked_reason": blocked_reason,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        decision_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return Decision(
            decision_id=decision_id,
            contract_type=contract_type,
            reason_codes=reason_codes,
            signal_epoch=signal_epoch,
            target_epoch=target_epoch,
            adx=adx,
            blocked_reason=blocked_reason,
            lane=self.lane,
        )
