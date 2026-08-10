"""Async M1 boundary alignment and pure entry-intent state transitions."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Awaitable, Callable, Mapping

from nexus_trade.constants import (
    NEXUS_DURATION_SECONDS,
    NEXUS_SYMBOL,
    NEXUS_TIMEFRAME_SECONDS,
)
from nexus_trade.domain import Lane
from nexus_trade.strategy import CONTRACT_TYPES, DECISION_BLOCKS, Decision


TARGET_MAX_DELAY_SECONDS = 1.0
ENTRY_MAX_DELAY_SECONDS = 2.0
ENTRY_STATUSES = frozenset({
    "PENDING",
    "DISPATCHED",
    "TARGET",
    "CONTINGENCY",
    "ACCEPTED_LATE",
    "STALE_BEFORE_DISPATCH",
    "PRE_DISPATCH_ERROR",
    "OWNERSHIP_QUARANTINE",
    *DECISION_BLOCKS,
})
ACCEPTED_STATUSES = frozenset({"TARGET", "CONTINGENCY", "ACCEPTED_LATE"})


def _finite_epoch(value: object, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite epoch")
    epoch = float(value)
    if not math.isfinite(epoch) or epoch < 0:
        raise ValueError(f"{name} must be a finite non-negative epoch")
    return epoch


def _exact_m1_epoch(value: object, name: str) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise TypeError(f"{name} must be an exact integer epoch")
    if value < 0 or value % NEXUS_TIMEFRAME_SECONDS:
        raise ValueError(f"{name} must be a non-negative M1-aligned epoch")
    return value


def _optional_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _finite_epoch(value, name)


def _nonempty(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _strict_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    keys = frozenset(value.keys())
    if keys != expected:
        raise ValueError(
            f"{name} keys mismatch; missing={sorted(expected - keys)}, "
            f"extra={sorted(keys - expected)}"
        )


@dataclass(frozen=True)
class DispatchReceipt:
    """A real dispatcher acknowledgement correlated to one sent intent."""

    decision_id: str
    contract_id: str
    dispatch_epoch: float
    accepted_epoch: float

    def __post_init__(self) -> None:
        _nonempty(self.decision_id, "decision_id")
        _nonempty(self.contract_id, "contract_id")
        dispatch = _finite_epoch(self.dispatch_epoch, "dispatch_epoch")
        accepted = _finite_epoch(self.accepted_epoch, "accepted_epoch")
        if accepted < dispatch:
            raise ValueError("accepted_epoch cannot precede dispatch_epoch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DispatchReceipt":
        _strict_keys(value, frozenset(cls.__dataclass_fields__), "DispatchReceipt")
        return cls(**dict(value))


@dataclass(frozen=True)
class EntryIntent:
    """Persistible, immutable lifecycle of an entry prepared after candle close."""

    decision_id: str
    contract_type: str | None
    reason_codes: tuple[str, ...]
    signal_epoch: int
    target_epoch: int
    adx: float | None
    prepared_epoch: float
    pre_dispatch_epoch: float | None
    dispatch_epoch: float | None
    accepted_epoch: float | None
    entry_delay_ms: float | None
    status: str
    error_code: str | None
    contract_id: str | None
    lane: str
    symbol: str = NEXUS_SYMBOL
    duration_seconds: int = NEXUS_DURATION_SECONDS

    def __post_init__(self) -> None:
        _nonempty(self.decision_id, "decision_id")
        if self.contract_type is not None and (
            type(self.contract_type) is not str or self.contract_type not in CONTRACT_TYPES
        ):
            raise ValueError("contract_type is invalid")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise TypeError("reason_codes must be a non-empty tuple")
        if any(type(reason) is not str or not reason for reason in self.reason_codes):
            raise TypeError("reason_codes must contain non-empty strings")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be deduplicated")
        signal = _exact_m1_epoch(self.signal_epoch, "signal_epoch")
        target = _exact_m1_epoch(self.target_epoch, "target_epoch")
        if target != signal + NEXUS_TIMEFRAME_SECONDS:
            raise ValueError("target_epoch must equal signal_epoch plus one M1")
        if self.adx is not None:
            _finite_epoch(self.adx, "adx")
        prepared = _finite_epoch(self.prepared_epoch, "prepared_epoch")
        if prepared < target:
            raise ValueError("prepared_epoch cannot be before target_epoch")
        dispatch = _finite_epoch(self.dispatch_epoch, "dispatch_epoch", optional=True)
        pre_dispatch = _finite_epoch(
            self.pre_dispatch_epoch, "pre_dispatch_epoch", optional=True,
        )
        if pre_dispatch is not None and pre_dispatch < max(float(target), prepared):
            raise ValueError("pre_dispatch_epoch cannot precede target_epoch or prepared_epoch")
        accepted = _finite_epoch(self.accepted_epoch, "accepted_epoch", optional=True)
        delay = _optional_number(self.entry_delay_ms, "entry_delay_ms")
        if dispatch is not None and dispatch < max(float(target), prepared):
            raise ValueError("dispatch_epoch cannot precede target_epoch or prepared_epoch")
        if accepted is not None and (dispatch is None or accepted < dispatch):
            raise ValueError("accepted_epoch requires and cannot precede dispatch_epoch")
        if type(self.status) is not str or self.status not in ENTRY_STATUSES:
            raise ValueError("status is invalid")
        _nonempty(self.error_code, "error_code", optional=True)
        _nonempty(self.contract_id, "contract_id", optional=True)
        if type(self.lane) is not str or self.lane not in {lane.value for lane in Lane}:
            raise ValueError("lane is invalid")
        if self.symbol != NEXUS_SYMBOL:
            raise ValueError("symbol is invalid")
        if isinstance(self.duration_seconds, bool) or type(self.duration_seconds) is not int:
            raise TypeError("duration_seconds must be an exact integer")
        if self.duration_seconds != NEXUS_DURATION_SECONDS:
            raise ValueError("duration_seconds is invalid")
        self._validate_status_timing(
            target, prepared, pre_dispatch, dispatch, accepted, delay,
        )

    def _validate_status_timing(
        self,
        target: int,
        prepared: float,
        pre_dispatch: float | None,
        dispatch: float | None,
        accepted: float | None,
        delay: float | None,
    ) -> None:
        no_io = DECISION_BLOCKS
        if self.status in no_io:
            if pre_dispatch is not None or dispatch is not None or accepted is not None or delay is not None:
                raise ValueError(f"{self.status} cannot carry I/O timing")
            if self.error_code is not None or self.contract_id is not None:
                raise ValueError(f"{self.status} cannot carry error or contract ownership")
            return
        if self.contract_type is None:
            raise ValueError(f"{self.status} requires a contract direction")
        if self.status == "PENDING":
            if any(value is not None for value in (
                pre_dispatch, dispatch, accepted, delay, self.error_code, self.contract_id,
            )):
                raise ValueError("PENDING must not invent dispatch, acceptance, delay, or ownership")
            return
        if self.status == "STALE_BEFORE_DISPATCH":
            if pre_dispatch is None:
                raise ValueError("STALE_BEFORE_DISPATCH requires a pre-dispatch check")
            expected = (pre_dispatch - target) * 1000.0
            if dispatch is not None or accepted is not None or self.contract_id is not None:
                raise ValueError("STALE_BEFORE_DISPATCH must not have been sent")
            if self.error_code is not None or delay is None or delay <= ENTRY_MAX_DELAY_SECONDS * 1000:
                raise ValueError("STALE_BEFORE_DISPATCH requires delay beyond the deadline")
            if not math.isclose(delay, expected, abs_tol=1e-6):
                raise ValueError("entry_delay_ms does not match prepared_epoch")
            return
        if self.status == "PRE_DISPATCH_ERROR":
            if pre_dispatch is not None or dispatch is not None or accepted is not None or delay is not None:
                raise ValueError("PRE_DISPATCH_ERROR cannot carry I/O timing")
            if self.error_code is None or self.contract_id is not None:
                raise ValueError("PRE_DISPATCH_ERROR requires only an error_code")
            return
        if dispatch is None:
            raise ValueError(f"{self.status} requires dispatch_epoch")
        if pre_dispatch is not None:
            raise ValueError(f"{self.status} cannot carry pre_dispatch_epoch")
        if self.status == "DISPATCHED":
            if any(value is not None for value in (
                accepted, delay, self.error_code, self.contract_id,
            )):
                raise ValueError("DISPATCHED has no receipt yet")
            return
        if self.status == "OWNERSHIP_QUARANTINE":
            if accepted is not None or delay is not None or self.contract_id is not None:
                raise ValueError("OWNERSHIP_QUARANTINE has ambiguous ownership")
            if self.error_code is None:
                raise ValueError("OWNERSHIP_QUARANTINE requires an error_code")
            return
        if self.status in ACCEPTED_STATUSES:
            if accepted is None or self.contract_id is None or delay is None:
                raise ValueError(f"{self.status} requires a complete real receipt")
            if self.error_code is not None:
                raise ValueError(f"{self.status} cannot carry error_code")
            expected = (accepted - target) * 1000.0
            if not math.isclose(delay, expected, abs_tol=1e-6):
                raise ValueError("entry_delay_ms does not match accepted_epoch")
            if self.status == "TARGET" and delay > TARGET_MAX_DELAY_SECONDS * 1000:
                raise ValueError("TARGET exceeds its inclusive deadline")
            if self.status == "CONTINGENCY" and not (
                TARGET_MAX_DELAY_SECONDS * 1000 < delay <= ENTRY_MAX_DELAY_SECONDS * 1000
            ):
                raise ValueError("CONTINGENCY is outside its interval")
            if self.status == "ACCEPTED_LATE" and delay <= ENTRY_MAX_DELAY_SECONDS * 1000:
                raise ValueError("ACCEPTED_LATE must exceed the absolute deadline")

    @property
    def classification(self) -> str:
        return self.status

    @property
    def blocked_reason(self) -> str | None:
        if self.status in ACCEPTED_STATUSES or self.status in {"PENDING", "DISPATCHED"}:
            return None
        return self.status

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EntryIntent":
        _strict_keys(value, frozenset(cls.__dataclass_fields__), "EntryIntent")
        reasons = value["reason_codes"]
        if not isinstance(reasons, (list, tuple)):
            raise TypeError("reason_codes must be a list or tuple")
        payload = dict(value)
        payload["reason_codes"] = tuple(reasons)
        return cls(**payload)

    def mark_dispatched(self, dispatch_epoch: float) -> "EntryIntent":
        self._require_status("PENDING")
        dispatch = _finite_epoch(dispatch_epoch, "dispatch_epoch")
        if dispatch < self.target_epoch:
            raise ValueError("dispatch_epoch cannot precede target_epoch")
        if dispatch < self.prepared_epoch:
            raise ValueError("dispatch_epoch cannot precede prepared_epoch")
        delay = dispatch - self.target_epoch
        if delay > ENTRY_MAX_DELAY_SECONDS:
            return replace(
                self,
                status="STALE_BEFORE_DISPATCH",
                pre_dispatch_epoch=dispatch,
                entry_delay_ms=round(delay * 1000.0, 6),
            )
        return replace(self, status="DISPATCHED", dispatch_epoch=dispatch)

    def mark_pre_dispatch_error(self, error_code: str) -> "EntryIntent":
        self._require_status("PENDING")
        _nonempty(error_code, "error_code")
        return replace(self, status="PRE_DISPATCH_ERROR", error_code=error_code)

    def mark_ownership_quarantine(self, error_code: str) -> "EntryIntent":
        self._require_status("DISPATCHED")
        _nonempty(error_code, "error_code")
        return replace(self, status="OWNERSHIP_QUARANTINE", error_code=error_code)

    def apply_receipt(self, receipt: DispatchReceipt) -> "EntryIntent":
        self._require_status("DISPATCHED")
        if type(receipt) is not DispatchReceipt:
            raise TypeError("receipt must be a DispatchReceipt")
        if receipt.decision_id != self.decision_id:
            raise ValueError("receipt decision_id does not match intent")
        if receipt.dispatch_epoch != self.dispatch_epoch:
            raise ValueError("receipt dispatch_epoch does not match sent intent")
        delay_ms = round((receipt.accepted_epoch - self.target_epoch) * 1000.0, 6)
        if delay_ms <= TARGET_MAX_DELAY_SECONDS * 1000:
            status = "TARGET"
        elif delay_ms <= ENTRY_MAX_DELAY_SECONDS * 1000:
            status = "CONTINGENCY"
        else:
            status = "ACCEPTED_LATE"
        return replace(
            self,
            accepted_epoch=receipt.accepted_epoch,
            entry_delay_ms=delay_ms,
            status=status,
            contract_id=receipt.contract_id,
        )

    def _require_status(self, expected: str) -> None:
        if self.status != expected:
            raise ValueError(f"intent is {self.status}, expected {expected}")


class EntryClock:
    """Deriv-epoch/monotonic alignment without candle or dispatcher I/O."""

    def __init__(
        self,
        *,
        epoch_now: Callable[[], float] = time.time,
        monotonic_now: Callable[[], float] = time.monotonic,
        async_sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._anchor_epoch = _finite_epoch(epoch_now(), "epoch anchor")
        self._anchor_monotonic = _finite_epoch(monotonic_now(), "monotonic anchor")
        self._monotonic_now = monotonic_now
        self._async_sleep = async_sleeper

    def current_epoch(self) -> float:
        monotonic = _finite_epoch(self._monotonic_now(), "monotonic reading")
        if monotonic < self._anchor_monotonic:
            raise ValueError("monotonic reading cannot precede its anchor")
        epoch = self._anchor_epoch + monotonic - self._anchor_monotonic
        return _finite_epoch(epoch, "aligned epoch")

    def next_boundary_epoch(self, *, after_epoch: int | None = None) -> int:
        if after_epoch is not None:
            return _exact_m1_epoch(after_epoch, "after_epoch") + NEXUS_TIMEFRAME_SECONDS
        now = self.current_epoch()
        lower = math.floor(now / NEXUS_TIMEFRAME_SECONDS) * NEXUS_TIMEFRAME_SECONDS
        return lower if math.isclose(now, lower, abs_tol=1e-9) else lower + NEXUS_TIMEFRAME_SECONDS

    async def await_boundary(self, target_epoch: int) -> int:
        target = _exact_m1_epoch(target_epoch, "target_epoch")
        target_monotonic = self._anchor_monotonic + target - self._anchor_epoch
        while True:
            monotonic = _finite_epoch(self._monotonic_now(), "monotonic reading")
            if monotonic < self._anchor_monotonic:
                raise ValueError("monotonic reading cannot precede its anchor")
            remaining = target_monotonic - monotonic
            if remaining <= 0:
                return target
            await self._async_sleep(remaining)

    def schedule(self, decision: Decision) -> EntryIntent:
        if type(decision) is not Decision:
            raise TypeError("schedule requires a validated Decision")
        prepared_epoch = self.current_epoch()
        if prepared_epoch < decision.target_epoch:
            raise ValueError("decision cannot be prepared before target candle close")
        status = decision.blocked_reason or "PENDING"
        delay = prepared_epoch - decision.target_epoch
        entry_delay_ms = None
        if status == "PENDING" and delay > ENTRY_MAX_DELAY_SECONDS:
            status = "STALE_BEFORE_DISPATCH"
            entry_delay_ms = round(delay * 1000.0, 6)
        return EntryIntent(
            decision_id=decision.decision_id,
            contract_type=decision.contract_type,
            reason_codes=decision.reason_codes,
            signal_epoch=decision.signal_epoch,
            target_epoch=decision.target_epoch,
            adx=decision.adx,
            prepared_epoch=prepared_epoch,
            pre_dispatch_epoch=(
                prepared_epoch if status == "STALE_BEFORE_DISPATCH" else None
            ),
            dispatch_epoch=None,
            accepted_epoch=None,
            entry_delay_ms=entry_delay_ms,
            status=status,
            error_code=None,
            contract_id=None,
            lane=decision.lane,
        )
