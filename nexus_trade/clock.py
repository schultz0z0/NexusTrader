"""Monotonic M1 boundary scheduler for NexusTrade entry decisions."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from nexus_trade.constants import (
    NEXUS_DURATION_SECONDS,
    NEXUS_SYMBOL,
    NEXUS_TIMEFRAME_SECONDS,
)
from nexus_trade.strategy import Decision


TARGET_MAX_DELAY_SECONDS = 1.0
ENTRY_MAX_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class EntryIntent:
    """Persistible timing outcome of scheduling one decision."""

    decision_id: str
    contract_type: str | None
    target_epoch: int
    dispatch_epoch: float | None
    accepted_epoch: float | None
    entry_delay_ms: float | None
    classification: str
    blocked_reason: str | None
    lane: str
    symbol: str = NEXUS_SYMBOL
    duration_seconds: int = NEXUS_DURATION_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EntryIntent":
        return cls(
            decision_id=str(value["decision_id"]),
            contract_type=value.get("contract_type"),
            target_epoch=int(value["target_epoch"]),
            dispatch_epoch=(
                None if value.get("dispatch_epoch") is None else float(value["dispatch_epoch"])
            ),
            accepted_epoch=(
                None if value.get("accepted_epoch") is None else float(value["accepted_epoch"])
            ),
            entry_delay_ms=(
                None if value.get("entry_delay_ms") is None else float(value["entry_delay_ms"])
            ),
            classification=str(value["classification"]),
            blocked_reason=value.get("blocked_reason"),
            lane=str(value["lane"]),
            symbol=str(value.get("symbol", NEXUS_SYMBOL)),
            duration_seconds=int(value.get("duration_seconds", NEXUS_DURATION_SECONDS)),
        )


class EntryClock:
    """Schedule against a Deriv-epoch anchor advanced only by monotonic time."""

    def __init__(
        self,
        *,
        epoch_now: Callable[[], float] = time.time,
        monotonic_now: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        finalize_bucket: Callable[[int, int], None] | None = None,
        dispatch: Callable[[Decision], float | int | None] | None = None,
    ):
        anchor_epoch = float(epoch_now())
        anchor_monotonic = float(monotonic_now())
        if not math.isfinite(anchor_epoch) or not math.isfinite(anchor_monotonic):
            raise ValueError("clock anchors must be finite")
        self._anchor_epoch = anchor_epoch
        self._anchor_monotonic = anchor_monotonic
        self._monotonic_now = monotonic_now
        self._sleep = sleeper
        self._finalize_bucket = finalize_bucket
        self._dispatch = dispatch

    def epoch_from_monotonic(self) -> float:
        monotonic = float(self._monotonic_now())
        if not math.isfinite(monotonic):
            raise ValueError("monotonic clock must be finite")
        return self._anchor_epoch + monotonic - self._anchor_monotonic

    def schedule(self, decision: Decision) -> EntryIntent:
        if (
            isinstance(decision.target_epoch, bool)
            or type(decision.target_epoch) is not int
            or decision.target_epoch % NEXUS_TIMEFRAME_SECONDS
        ):
            raise ValueError("target_epoch must align to M1")
        if decision.blocked_reason is not None or decision.contract_type is None:
            classification = decision.blocked_reason or "NO_TRADE"
            return self._intent(
                decision,
                dispatch_epoch=None,
                accepted_epoch=None,
                entry_delay_ms=None,
                classification=classification,
                blocked_reason=classification,
            )

        target_monotonic = (
            self._anchor_monotonic + decision.target_epoch - self._anchor_epoch
        )
        while True:
            remaining = target_monotonic - float(self._monotonic_now())
            if remaining <= 0:
                break
            self._sleep(remaining)

        if self._finalize_bucket is not None:
            self._finalize_bucket(
                decision.target_epoch - NEXUS_TIMEFRAME_SECONDS,
                decision.target_epoch,
            )

        candidate_dispatch_epoch = self.epoch_from_monotonic()
        dispatch_delay = candidate_dispatch_epoch - decision.target_epoch
        if dispatch_delay > ENTRY_MAX_DELAY_SECONDS:
            return self._intent(
                decision,
                dispatch_epoch=None,
                accepted_epoch=None,
                entry_delay_ms=self._milliseconds(dispatch_delay),
                classification="STALE",
                blocked_reason="STALE",
            )

        accepted_override = None
        if self._dispatch is not None:
            accepted_override = self._dispatch(decision)
        if accepted_override is None:
            accepted_epoch = self.epoch_from_monotonic()
        else:
            if isinstance(accepted_override, bool):
                raise ValueError("accepted epoch must be finite")
            accepted_epoch = float(accepted_override)
            if not math.isfinite(accepted_epoch):
                raise ValueError("accepted epoch must be finite")
        entry_delay = accepted_epoch - decision.target_epoch
        if entry_delay <= TARGET_MAX_DELAY_SECONDS:
            classification = "TARGET"
            blocked_reason = None
        elif entry_delay <= ENTRY_MAX_DELAY_SECONDS:
            classification = "CONTINGENCY"
            blocked_reason = None
        else:
            classification = "STALE"
            blocked_reason = "STALE"
        return self._intent(
            decision,
            dispatch_epoch=candidate_dispatch_epoch,
            accepted_epoch=accepted_epoch,
            entry_delay_ms=self._milliseconds(entry_delay),
            classification=classification,
            blocked_reason=blocked_reason,
        )

    @staticmethod
    def _milliseconds(delay_seconds: float) -> float:
        return round(max(0.0, delay_seconds) * 1000.0, 6)

    @staticmethod
    def _intent(
        decision: Decision,
        *,
        dispatch_epoch: float | None,
        accepted_epoch: float | None,
        entry_delay_ms: float | None,
        classification: str,
        blocked_reason: str | None,
    ) -> EntryIntent:
        return EntryIntent(
            decision_id=decision.decision_id,
            contract_type=decision.contract_type,
            target_epoch=decision.target_epoch,
            dispatch_epoch=dispatch_epoch,
            accepted_epoch=accepted_epoch,
            entry_delay_ms=entry_delay_ms,
            classification=classification,
            blocked_reason=blocked_reason,
            lane=decision.lane,
        )
