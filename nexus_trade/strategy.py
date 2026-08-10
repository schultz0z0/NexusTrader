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
POSITION_STATUSES = frozenset({"IDLE", "RESERVED", "ACTIVE", "QUARANTINED"})
DECISION_BLOCKS = frozenset({"NO_TRADE", "ADX_BLOCKED", "POSITION_ACTIVE"})
CONTRACT_TYPES = frozenset({"CALL", "PUT"})
RECONCILIATION_OUTCOMES = frozenset({"CONTRACT_FOUND", "PURCHASE_ABSENT"})


def _exact_epoch(value: object, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or type(value) is not int:
        raise TypeError(f"{name} must be an exact integer epoch")
    if value < 0 or value % NEXUS_TIMEFRAME_SECONDS:
        raise ValueError(f"{name} must be a non-negative M1-aligned epoch")
    return value


def _finite_float(value: object, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _nonempty_string(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _positive_contract_id(value: object, name: str = "contract_id", *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _strict_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    keys = frozenset(value.keys())
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ValueError(f"{name} keys mismatch; missing={missing}, extra={extra}")


@dataclass(frozen=True)
class OwnershipReconciliation:
    """Correlated, unequivocal result from the ownership coordinator."""

    correlation_id: str
    decision_id: str
    outcome: str
    contract_id: int | None

    def __post_init__(self) -> None:
        _nonempty_string(self.correlation_id, "correlation_id")
        _nonempty_string(self.decision_id, "decision_id")
        if type(self.outcome) is not str or self.outcome not in RECONCILIATION_OUTCOMES:
            raise ValueError("reconciliation outcome is invalid")
        _positive_contract_id(self.contract_id, optional=True)
        if self.outcome == "CONTRACT_FOUND" and self.contract_id is None:
            raise ValueError("CONTRACT_FOUND requires contract_id")
        if self.outcome == "PURCHASE_ABSENT" and self.contract_id is not None:
            raise ValueError("PURCHASE_ABSENT cannot carry contract_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OwnershipReconciliation":
        _strict_keys(value, frozenset(cls.__dataclass_fields__), "OwnershipReconciliation")
        return cls(**dict(value))


@dataclass(frozen=True)
class SetupState:
    """Primitive-only state needed to resume strategy and ownership exactly."""

    upper_break_epoch: int | None = None
    lower_break_epoch: int | None = None
    previous_upper: float | None = None
    previous_lower: float | None = None
    last_candle_epoch: int | None = None
    position_status: str = "IDLE"
    owner_decision_id: str | None = None
    contract_id: int | None = None
    reconciliation_id: str | None = None
    reconciliation_decision_id: str | None = None
    reconciliation_outcome: str | None = None

    def __post_init__(self) -> None:
        _exact_epoch(self.upper_break_epoch, "upper_break_epoch", optional=True)
        _exact_epoch(self.lower_break_epoch, "lower_break_epoch", optional=True)
        _finite_float(self.previous_upper, "previous_upper", optional=True)
        _finite_float(self.previous_lower, "previous_lower", optional=True)
        _exact_epoch(self.last_candle_epoch, "last_candle_epoch", optional=True)
        if type(self.position_status) is not str or self.position_status not in POSITION_STATUSES:
            raise ValueError("position_status is invalid")
        _nonempty_string(self.owner_decision_id, "owner_decision_id", optional=True)
        _positive_contract_id(self.contract_id, optional=True)
        _nonempty_string(self.reconciliation_id, "reconciliation_id", optional=True)
        _nonempty_string(
            self.reconciliation_decision_id,
            "reconciliation_decision_id",
            optional=True,
        )
        if self.reconciliation_outcome is not None and (
            type(self.reconciliation_outcome) is not str
            or self.reconciliation_outcome not in RECONCILIATION_OUTCOMES
        ):
            raise ValueError("reconciliation_outcome is invalid")
        reconciliation_values = (
            self.reconciliation_id,
            self.reconciliation_decision_id,
            self.reconciliation_outcome,
        )
        if any(value is None for value in reconciliation_values) and any(
            value is not None for value in reconciliation_values
        ):
            raise ValueError("reconciliation identity and outcome must be complete")
        if self.position_status == "IDLE":
            if self.owner_decision_id is not None or self.contract_id is not None:
                raise ValueError("IDLE state cannot have an owner or contract")
        elif self.position_status == "RESERVED":
            if self.owner_decision_id is None or self.contract_id is not None:
                raise ValueError("RESERVED state requires only an owner")
        elif self.position_status == "ACTIVE":
            if self.owner_decision_id is None or self.contract_id is None:
                raise ValueError("ACTIVE state requires owner and contract")
        elif self.owner_decision_id is None:
            raise ValueError("QUARANTINED state requires an owner")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SetupState":
        expected = frozenset(cls.__dataclass_fields__)
        _strict_keys(value, expected, "SetupState")
        return cls(**dict(value))


@dataclass(frozen=True)
class Decision:
    """Strict persistible result for every evaluated closed candle."""

    decision_id: str
    contract_type: str | None
    reason_codes: tuple[str, ...]
    signal_epoch: int
    target_epoch: int
    adx: float | None
    blocked_reason: str | None
    lane: str = Lane.CHAMPION.value

    def __post_init__(self) -> None:
        _nonempty_string(self.decision_id, "decision_id")
        if self.contract_type is not None and (
            type(self.contract_type) is not str or self.contract_type not in CONTRACT_TYPES
        ):
            raise ValueError("contract_type is invalid")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise TypeError("reason_codes must be a non-empty tuple")
        if any(type(code) is not str or not code for code in self.reason_codes):
            raise TypeError("reason_codes must contain non-empty strings")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be deduplicated")
        signal_epoch = _exact_epoch(self.signal_epoch, "signal_epoch")
        target_epoch = _exact_epoch(self.target_epoch, "target_epoch")
        if target_epoch != signal_epoch + NEXUS_TIMEFRAME_SECONDS:
            raise ValueError("target_epoch must equal signal_epoch plus one M1")
        adx = _finite_float(self.adx, "adx", optional=True)
        if adx is not None and not 0.0 <= adx <= 100.0:
            raise ValueError("adx must be between 0 and 100")
        if self.blocked_reason is not None and (
            type(self.blocked_reason) is not str or self.blocked_reason not in DECISION_BLOCKS
        ):
            raise ValueError("blocked_reason is invalid")
        if type(self.lane) is not str or self.lane not in {lane.value for lane in Lane}:
            raise ValueError("lane is invalid")
        if self.contract_type is None and self.blocked_reason != "NO_TRADE":
            raise ValueError("a decision without direction must be NO_TRADE")
        if self.contract_type is not None and self.blocked_reason == "NO_TRADE":
            raise ValueError("NO_TRADE cannot carry a contract direction")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Decision":
        expected = frozenset(cls.__dataclass_fields__)
        _strict_keys(value, expected, "Decision")
        reasons = value["reason_codes"]
        if not isinstance(reasons, (list, tuple)):
            raise TypeError("reason_codes must be a list or tuple")
        payload = dict(value)
        payload["reason_codes"] = tuple(reasons)
        return cls(**payload)


def _has_field(value: object, name: str) -> bool:
    return name in value if isinstance(value, Mapping) else hasattr(value, name)


def _field(value: object, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    raise ValueError(f"missing required field: {names[0]}")


def _market_number(value: object, name: str, *, optional: bool = False) -> float | None:
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


def _validate_closed_candle(candle: object, epoch: int) -> None:
    evidence = False
    markers: list[bool] = []
    for name in ("is_closed", "closed"):
        if _has_field(candle, name):
            marker = _field(candle, name)
            if type(marker) is not bool:
                raise ValueError(f"{name} must be an exact boolean")
            markers.append(marker)
            evidence = evidence or marker
    if markers and (not all(markers)):
        raise ValueError("candle closure markers conflict or identify a live candle")
    if _has_field(candle, "close_epoch"):
        close_epoch = _field(candle, "close_epoch")
        if isinstance(close_epoch, bool) or type(close_epoch) is not int:
            raise ValueError("close_epoch must be an exact integer M1 close")
        if close_epoch != epoch + NEXUS_TIMEFRAME_SECONDS:
            raise ValueError("close_epoch must match the causal M1 close")
        evidence = True
    if not evidence:
        raise ValueError("closed candle evidence is required")


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

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()

    def release_reservation(self, owner_decision_id: str) -> None:
        self._require_owner("RESERVED", owner_decision_id)
        self.state = replace(
            self.state,
            position_status="IDLE",
            owner_decision_id=None,
            contract_id=None,
        )

    def mark_position_active(self, owner_decision_id: str, contract_id: int) -> None:
        self._require_owner("RESERVED", owner_decision_id)
        _positive_contract_id(contract_id)
        self.state = replace(
            self.state,
            position_status="ACTIVE",
            contract_id=contract_id,
        )

    def mark_position_quarantined(self, owner_decision_id: str) -> None:
        self._require_owner("RESERVED", owner_decision_id)
        self.state = replace(self.state, position_status="QUARANTINED")

    def reconcile_quarantine(self, result: OwnershipReconciliation) -> None:
        if type(result) is not OwnershipReconciliation:
            raise TypeError("result must be an OwnershipReconciliation")
        if self.state.reconciliation_id == result.correlation_id:
            if (
                self.state.reconciliation_decision_id != result.decision_id
                or self.state.reconciliation_outcome != result.outcome
            ):
                raise ValueError("reconciliation correlation conflicts with persisted result")
            if result.outcome == "CONTRACT_FOUND":
                if (
                    self.state.position_status == "ACTIVE"
                    and self.state.owner_decision_id == result.decision_id
                    and self.state.contract_id == result.contract_id
                ):
                    return
            elif self.state.position_status == "IDLE":
                return
            raise ValueError("persisted reconciliation no longer matches ownership state")
        if self.state.position_status != "QUARANTINED":
            raise ValueError(
                f"position is {self.state.position_status}, expected QUARANTINED"
            )
        if result.decision_id != self.state.owner_decision_id:
            raise ValueError("owner decision does not match quarantined position")
        common = {
            "reconciliation_id": result.correlation_id,
            "reconciliation_decision_id": result.decision_id,
            "reconciliation_outcome": result.outcome,
        }
        if result.outcome == "CONTRACT_FOUND":
            self.state = replace(
                self.state,
                position_status="ACTIVE",
                contract_id=result.contract_id,
                **common,
            )
        else:
            self.state = replace(
                self.state,
                position_status="IDLE",
                owner_decision_id=None,
                contract_id=None,
                **common,
            )

    def mark_position_closed(self, owner_decision_id: str, contract_id: int) -> None:
        if self.state.position_status == "QUARANTINED":
            raise ValueError("QUARANTINED ownership must be reconciled before close")
        self._require_owner("ACTIVE", owner_decision_id)
        _positive_contract_id(contract_id)
        if contract_id != self.state.contract_id:
            raise ValueError("contract identity does not own the active position")
        self.state = replace(
            self.state,
            position_status="IDLE",
            owner_decision_id=None,
            contract_id=None,
        )

    def _require_owner(self, required_status: str, owner_decision_id: str) -> None:
        _nonempty_string(owner_decision_id, "owner_decision_id")
        if self.state.position_status != required_status:
            raise ValueError(
                f"position is {self.state.position_status}, expected {required_status}"
            )
        if owner_decision_id != self.state.owner_decision_id:
            raise ValueError("owner decision does not match the position reservation")

    def on_closed_candle(
        self,
        candle: object,
        indicators: IndicatorFrame | Mapping[str, Any],
        *,
        causal_epoch: int | None = None,
    ) -> list[Decision]:
        epoch = _field(candle, "open_epoch", "time")
        epoch = _exact_epoch(epoch, "closed candle epoch")
        if _has_field(candle, "open_epoch") and _has_field(candle, "time"):
            if _field(candle, "open_epoch") != _field(candle, "time"):
                raise ValueError("candle epoch fields conflict")
        _validate_closed_candle(candle, epoch)
        if causal_epoch is not None:
            causal_epoch = _exact_epoch(causal_epoch, "causal_epoch")
            if epoch + NEXUS_TIMEFRAME_SECONDS > causal_epoch:
                raise ValueError("candle closes after causal_epoch")
        if self.state.last_candle_epoch is not None and epoch <= self.state.last_candle_epoch:
            raise ValueError("closed candle epochs must be strictly increasing")

        indicator_epoch = _field(indicators, "epoch")
        _exact_epoch(indicator_epoch, "indicator epoch")
        if indicator_epoch != epoch:
            raise ValueError("indicator frame must belong to the closed candle")
        opening = _market_number(_field(candle, "open"), "open")
        close = _market_number(_field(candle, "close"), "close")
        upper = _market_number(_field(indicators, "upper"), "upper", optional=True)
        middle = _market_number(_field(indicators, "middle"), "middle", optional=True)
        lower = _market_number(_field(indicators, "lower"), "lower", optional=True)
        adx = _market_number(_field(indicators, "adx"), "adx", optional=True)
        if adx is not None and not 0.0 <= adx <= 100.0:
            raise ValueError("adx must be between 0 and 100")

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

        lane_idle = self.state.position_status == "IDLE"
        upper_started = (
            lane_idle
            and upper_break_epoch is None
            and upper is not None
            and self.state.previous_upper is not None
            and close > upper
            and upper > self.state.previous_upper
        )
        lower_started = (
            lane_idle
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
        reserve = False
        if contract_type is None:
            blocked_reason = "NO_TRADE"
            audit_reasons = self._no_trade_reasons(
                upper_started=upper_started,
                lower_started=lower_started,
                upper_break_epoch=upper_break_epoch,
                lower_break_epoch=lower_break_epoch,
            )
            reason_codes.extend(code for code in audit_reasons if code not in reason_codes)
        elif not lane_idle:
            blocked_reason = "POSITION_ACTIVE"
            upper_break_epoch = None
            lower_break_epoch = None
        elif adx is None or adx > ADX_ENTRY_MAX:
            blocked_reason = "ADX_BLOCKED"
            upper_break_epoch = None
            lower_break_epoch = None
        else:
            blocked_reason = None
            reserve = True
            upper_break_epoch = None
            lower_break_epoch = None

        decision = self._decision(
            contract_type=contract_type,
            reason_codes=tuple(reason_codes),
            signal_epoch=epoch,
            adx=adx,
            blocked_reason=blocked_reason,
        )
        self.state = SetupState(
            upper_break_epoch=upper_break_epoch,
            lower_break_epoch=lower_break_epoch,
            previous_upper=upper,
            previous_lower=lower,
            last_candle_epoch=epoch,
            position_status="RESERVED" if reserve else self.state.position_status,
            owner_decision_id=decision.decision_id if reserve else self.state.owner_decision_id,
            contract_id=self.state.contract_id,
            reconciliation_id=self.state.reconciliation_id,
            reconciliation_decision_id=self.state.reconciliation_decision_id,
            reconciliation_outcome=self.state.reconciliation_outcome,
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
        encoded = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
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
