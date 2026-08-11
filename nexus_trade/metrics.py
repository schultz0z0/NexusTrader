"""Exact, fail-closed metrics for settled NexusTrade lane contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class MetricIntegrityError(ValueError):
    """A settlement cannot safely participate in governed metrics."""


@dataclass(frozen=True)
class LaneMetrics:
    n_total: int
    n_decisive: int
    wins: int
    losses: int
    ties: int
    accuracy: float | None
    capital_at_risk: float
    total_stake: float
    total_payout: float
    total_profit: float
    normalized_expectancy: float | None
    average_payout: float | None
    payout_distribution: tuple[float, ...]
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    max_drawdown: float
    max_drawdown_normalized: float | None
    recovery_factor: float | None
    worst_rolling_50: float | None
    worst_rolling_50_normalized: float | None
    max_loss_streak: int

    @property
    def expectancy(self) -> float | None:
        return self.normalized_expectancy

    @property
    def accuracy_percent(self) -> float | None:
        return None if self.accuracy is None else self.accuracy * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _finite_number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricIntegrityError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise MetricIntegrityError(f"{field} must be {'positive and ' if positive else ''}finite")
    return number


def calculate_lane_metrics(settlements: Iterable[Any]) -> LaneMetrics:
    """Aggregate raw counts and money, then derive ratios without fake infinities."""
    if isinstance(settlements, (str, bytes, Mapping)):
        raise TypeError("settlements must be an iterable of settlement rows")
    try:
        rows = list(settlements)
    except TypeError as exc:
        raise TypeError("settlements must be iterable") from exc

    wins = losses = ties = 0
    total_stake = total_payout = total_profit = 0.0
    gross_profit = gross_loss = 0.0
    equity = peak = max_drawdown = 0.0
    current_loss_streak = max_loss_streak = 0
    profits: list[float] = []
    stakes: list[float] = []
    payout_ratios: list[float] = []

    for row in rows:
        if _value(row, "settled", True) is not True:
            raise MetricIntegrityError("only durably settled contracts may be measured")
        result = _value(row, "result")
        if result not in {"won", "lost", "tie"}:
            raise MetricIntegrityError("result must be won, lost or tie")
        stake = _finite_number(_value(row, "stake"), "stake", positive=True)
        payout = _finite_number(_value(row, "payout"), "payout")
        profit = _finite_number(_value(row, "profit"), "profit")
        if payout < 0:
            raise MetricIntegrityError("payout cannot be negative")
        if result == "won" and profit <= 0:
            raise MetricIntegrityError("won settlement must have positive realized profit")
        if result == "lost" and profit >= 0:
            raise MetricIntegrityError("lost settlement must have negative realized profit")

        wins += result == "won"
        losses += result == "lost"
        ties += result == "tie"
        total_stake += stake
        total_payout += payout
        total_profit += profit
        gross_profit += max(profit, 0.0)
        gross_loss += max(-profit, 0.0)
        profits.append(profit)
        stakes.append(stake)
        payout_ratios.append(payout / stake)

        equity += profit
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if result == "lost":
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0

    n_total = len(rows)
    n_decisive = wins + losses
    accuracy = wins / n_decisive if n_decisive else None
    normalized_expectancy = total_profit / total_stake if total_stake else None
    average_payout = total_payout / total_stake if total_stake else None
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    recovery_factor = total_profit / max_drawdown if max_drawdown > 0 else None
    max_drawdown_normalized = max_drawdown / total_stake if total_stake else None

    worst_rolling_50 = None
    worst_rolling_50_normalized = None
    if n_total >= 50:
        windows = [
            (sum(profits[index:index + 50]), sum(stakes[index:index + 50]))
            for index in range(n_total - 49)
        ]
        worst_rolling_50, block_stake = min(windows, key=lambda item: item[0])
        worst_rolling_50_normalized = worst_rolling_50 / block_stake

    return LaneMetrics(
        n_total=n_total,
        n_decisive=n_decisive,
        wins=wins,
        losses=losses,
        ties=ties,
        accuracy=accuracy,
        capital_at_risk=total_stake,
        total_stake=total_stake,
        total_payout=total_payout,
        total_profit=total_profit,
        normalized_expectancy=normalized_expectancy,
        average_payout=average_payout,
        payout_distribution=tuple(sorted(payout_ratios)),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        max_drawdown_normalized=max_drawdown_normalized,
        recovery_factor=recovery_factor,
        worst_rolling_50=worst_rolling_50,
        worst_rolling_50_normalized=worst_rolling_50_normalized,
        max_loss_streak=max_loss_streak,
    )


__all__ = ["LaneMetrics", "MetricIntegrityError", "calculate_lane_metrics"]
