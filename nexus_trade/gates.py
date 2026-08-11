"""Conservative, explainable promotion recommendation gates."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from nexus_trade.metrics import LaneMetrics


GateStatus = Literal["PASS", "FAIL", "INCONCLUSIVE"]


@dataclass(frozen=True)
class GateResult:
    code: str
    status: GateStatus
    observed: Any
    threshold: Any
    reason: str


@dataclass(frozen=True)
class PromotionEvaluation:
    gates: tuple[GateResult, ...]
    recommendation: Literal["EVOLVE", "REANALYZE", "INCONCLUSIVE"]
    reasons: tuple[str, ...]


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


class PromotionGateEvaluator:
    """Evaluate forward DEMO evidence without independent-trade resampling."""

    def __init__(self, *, seed: int = 0, bootstrap_samples: int = 5000):
        if type(seed) is not int or type(bootstrap_samples) is not int or bootstrap_samples < 100:
            raise ValueError("seed and at least 100 bootstrap samples are required")
        self.seed = seed
        self.bootstrap_samples = bootstrap_samples

    def evaluate(
        self,
        champion: LaneMetrics,
        trial: LaneMetrics,
        context: Mapping[str, Any],
    ) -> PromotionEvaluation:
        if type(champion) is not LaneMetrics or type(trial) is not LaneMetrics:
            raise TypeError("champion and trial must be LaneMetrics")
        if not isinstance(context, Mapping):
            raise TypeError("context must be a mapping")

        gates: list[GateResult] = []
        complete_days = context.get("complete_days")
        settled = context.get("trial_settled_operations", trial.n_total)
        sample_ok = (
            type(complete_days) is int and complete_days >= 7
            and type(settled) is int and settled >= 300
            and trial.n_total >= 300
        )
        gates.append(GateResult(
            "MINIMUM_SAMPLE", "PASS" if sample_ok else "INCONCLUSIVE",
            {"complete_days": complete_days, "settled_trial": settled},
            {"complete_days": 7, "settled_trial": 300},
            "seven complete days and 300 durably settled Trial operations are present"
            if sample_ok else "campaign is still accumulating seven complete days and 300 settled Trial operations",
        ))

        integrity = context.get("integrity")
        required_integrity = (
            "trial_frozen", "all_reconciled", "no_duplicates", "no_future_leakage",
            "dispatch_within_limit", "reproducible", "risk_limits_ok",
        )
        coverage = integrity.get("candle_coverage") if isinstance(integrity, Mapping) else None
        missing_integrity = [
            name for name in required_integrity
            if not isinstance(integrity, Mapping) or integrity.get(name) is not True
        ]
        integrity_ok = not missing_integrity and _finite(coverage) and float(coverage) >= 0.995
        gates.append(GateResult(
            "DATA_INTEGRITY", "PASS" if integrity_ok else "FAIL",
            {"candle_coverage": coverage, "failed_checks": tuple(missing_integrity)},
            "all checks true; candle coverage >= 0.995",
            "all contracts and provenance are reconciled and reproducible"
            if integrity_ok else "missing, ambiguous or invalid integrity evidence",
        ))

        comparable, comparison_reason = self._comparable(context)
        gates.append(GateResult(
            "COMPARABLE_PROVENANCE", "PASS" if comparable else "INCONCLUSIVE",
            comparable, "same R_100/M1/58s window and provenance",
            comparison_reason,
        ))

        trial_ev = trial.normalized_expectancy
        champion_ev = champion.normalized_expectancy
        gates.append(self._numeric_gate(
            "TRIAL_EXPECTANCY_POSITIVE", trial_ev, 0.0,
            lambda value: value > 0,
            "Trial normalized expectancy must be positive",
        ))
        gates.append(self._numeric_gate(
            "PROFIT_FACTOR", trial.profit_factor, 1.10,
            lambda value: value >= 1.10,
            "Trial profit factor must be at least 1.10",
        ))
        improvement = (
            trial_ev - champion_ev
            if _finite(trial_ev) and _finite(champion_ev) else None
        )
        gates.append(self._numeric_gate(
            "EXPECTANCY_IMPROVEMENT", improvement, 0.02,
            lambda value: value >= 0.02,
            "Trial must improve normalized expectancy by at least 2 percentage points",
        ))
        gates.append(self._bootstrap_gate(context.get("temporal_blocks")))

        gates.extend(self._risk_gates(champion, trial, integrity))
        gates.extend(self._stability_gates(context))
        gates.append(self._numeric_gate(
            "DSR", context.get("dsr_probability"), 0.95,
            lambda value: value >= 0.95,
            "deflated positive-performance probability must be at least 95%",
        ))
        gates.append(self._numeric_gate(
            "PBO", context.get("pbo"), 0.10,
            lambda value: value <= 0.10,
            "probability of backtest overfitting must be at most 10%",
        ))
        sensitivity = context.get("sensitivity_passed")
        gates.append(GateResult(
            "SENSITIVITY", "PASS" if sensitivity is True else "INCONCLUSIVE" if sensitivity is None else "FAIL",
            sensitivity, True, "candidate remains superior under sensitivity analysis"
            if sensitivity is True else "sensitivity evidence is missing or failed",
        ))
        families = context.get("change_families")
        family_count = len(set(families)) if isinstance(families, (list, tuple, set)) else None
        bollinger = context.get("bollinger_present")
        ablation = context.get("new_indicator_ablation_passed")
        ablation_required = (
            isinstance(families, (list, tuple, set))
            and "indicator_addition" in families
        )
        ablation_ok = ablation is True if ablation_required else True
        change_ok = family_count is not None and family_count <= 1 and bollinger is True and ablation_ok
        change_inconclusive = (
            family_count is None
            or (ablation_required and ablation is None)
        )
        gates.append(GateResult(
            "CHANGE_BUDGET", "PASS" if change_ok else "INCONCLUSIVE" if change_inconclusive else "FAIL",
            {"families": family_count, "bollinger": bollinger, "ablation": ablation, "ablation_required": ablation_required},
            "at most one material family; Bollinger retained; ablation passed",
            "change budget and causal attribution are satisfied" if change_ok else "change budget, Bollinger or ablation requirement failed",
        ))

        failed = [gate for gate in gates if gate.status == "FAIL"]
        inconclusive = [gate for gate in gates if gate.status == "INCONCLUSIVE"]
        if not sample_ok or not integrity_ok or not comparable or inconclusive:
            recommendation = "INCONCLUSIVE"
        elif failed:
            recommendation = "REANALYZE"
        else:
            recommendation = "EVOLVE"
        reasons = tuple(gate.reason for gate in gates if gate.status != "PASS")
        return PromotionEvaluation(tuple(gates), recommendation, reasons)

    @staticmethod
    def _numeric_gate(code, observed, threshold, predicate, reason) -> GateResult:
        if not _finite(observed):
            return GateResult(code, "INCONCLUSIVE", observed, threshold, f"{reason}; finite evidence is unavailable")
        passed = predicate(float(observed))
        return GateResult(code, "PASS" if passed else "FAIL", float(observed), threshold, reason)

    @staticmethod
    def _comparable(context: Mapping[str, Any]) -> tuple[bool, str]:
        champion = context.get("champion_provenance")
        trial = context.get("trial_provenance")
        if not isinstance(champion, Mapping) or not isinstance(trial, Mapping):
            return False, "Champion or Trial provenance is missing"
        required = (
            "symbol", "timeframe_seconds", "duration_seconds", "window_start",
            "window_end", "campaign_id", "version_id", "provenance_hash",
        )
        if any(not champion.get(name) or not trial.get(name) for name in required):
            return False, "Champion or Trial provenance is incomplete"
        contract = ("R_100", 60, 58)
        if tuple(champion[name] for name in required[:3]) != contract or tuple(trial[name] for name in required[:3]) != contract:
            return False, "comparison contract must be R_100/M1/58s"
        if (champion["window_start"], champion["window_end"]) != (trial["window_start"], trial["window_end"]):
            return False, "Champion and Trial window boundaries do not match"
        if champion["provenance_hash"] != trial["provenance_hash"]:
            return False, "Champion and Trial provenance hashes do not match"
        if champion["campaign_id"] == trial["campaign_id"] or champion["version_id"] == trial["version_id"]:
            return False, "separate Champion and Trial campaign/version identities are required"
        return True, "Champion and Trial use separate identities on one comparable window"

    def _bootstrap_gate(self, blocks: Any) -> GateResult:
        if not isinstance(blocks, (list, tuple)) or len(blocks) < 7:
            return GateResult(
                "BLOCK_BOOTSTRAP_95", "INCONCLUSIVE", None,
                {"lower_bound": "> 0", "probability_superior": ">= 0.95"},
                "at least seven paired temporal blocks are required; independent-trade bootstrap is forbidden",
            )
        differences = []
        for block in blocks:
            if not isinstance(block, Mapping) or not _finite(block.get("champion")) or not _finite(block.get("trial")):
                return GateResult("BLOCK_BOOTSTRAP_95", "INCONCLUSIVE", None, "paired finite temporal blocks", "temporal block evidence is malformed")
            differences.append(float(block["trial"]) - float(block["champion"]))
        rng = random.Random(self.seed)
        estimates = sorted(
            sum(rng.choice(differences) for _ in differences) / len(differences)
            for _ in range(self.bootstrap_samples)
        )
        lower = estimates[max(0, math.ceil(0.05 * len(estimates)) - 1)]
        probability = sum(value > 0 for value in estimates) / len(estimates)
        observed = {"lower_bound": lower, "probability_superior": probability, "block_count": len(differences)}
        passed = lower > 0 and probability >= 0.95
        return GateResult("BLOCK_BOOTSTRAP_95", "PASS" if passed else "INCONCLUSIVE", observed, {"lower_bound": "> 0", "probability_superior": ">= 0.95"}, "paired temporal block bootstrap preserves ordering dependence")

    def _risk_gates(self, champion: LaneMetrics, trial: LaneMetrics, integrity: Any) -> list[GateResult]:
        return [
            self._relative_upper("DRAWDOWN", trial.max_drawdown_normalized, champion.max_drawdown_normalized, 0.05, "normalized drawdown may be at most 5% worse"),
            self._relative_lower("RECOVERY", trial.recovery_factor, champion.recovery_factor, 0.0, "recovery factor may not be lower"),
            self._relative_lower("WORST_ROLLING_50", trial.worst_rolling_50_normalized, champion.worst_rolling_50_normalized, 0.10, "worst rolling 50 may be at most 10% worse"),
            GateResult("LOSS_STREAK", "PASS" if trial.max_loss_streak <= champion.max_loss_streak + 2 else "FAIL", trial.max_loss_streak, champion.max_loss_streak + 2, "Trial loss streak may exceed Champion by at most two"),
            GateResult("RISK_LIMITS", "PASS" if isinstance(integrity, Mapping) and integrity.get("risk_limits_ok") is True else "FAIL", isinstance(integrity, Mapping) and integrity.get("risk_limits_ok"), True, "no stake/account/symbol/expiry/concurrency violation is allowed"),
        ]

    @staticmethod
    def _relative_upper(code, trial, champion, tolerance, reason):
        if not _finite(trial) or not _finite(champion):
            return GateResult(code, "INCONCLUSIVE", trial, f"Champion + {tolerance:.0%}", reason)
        threshold = float(champion) + abs(float(champion)) * tolerance
        return GateResult(code, "PASS" if float(trial) <= threshold else "FAIL", float(trial), threshold, reason)

    @staticmethod
    def _relative_lower(code, trial, champion, tolerance, reason):
        if not _finite(trial) or not _finite(champion):
            return GateResult(code, "INCONCLUSIVE", trial, f"Champion - {tolerance:.0%}", reason)
        threshold = float(champion) - abs(float(champion)) * tolerance
        return GateResult(code, "PASS" if float(trial) >= threshold else "FAIL", float(trial), threshold, reason)

    @staticmethod
    def _stability_gates(context: Mapping[str, Any]) -> list[GateResult]:
        daily = context.get("daily")
        if not isinstance(daily, (list, tuple)) or not daily:
            daily_status = "INCONCLUSIVE"
            observed = None
        else:
            valid = all(
                isinstance(day, Mapping)
                and _finite(day.get("champion_expectancy"))
                and _finite(day.get("trial_expectancy"))
                and _finite(day.get("trial_profit"))
                for day in daily
            )
            if not valid:
                daily_status, observed = "INCONCLUSIVE", None
            else:
                superior = sum(day["trial_expectancy"] > day["champion_expectancy"] for day in daily) / len(daily)
                positive = sum(day["trial_expectancy"] > 0 for day in daily) / len(daily)
                positive_profit = sum(max(float(day["trial_profit"]), 0.0) for day in daily)
                max_share = max((max(float(day["trial_profit"]), 0.0) for day in daily), default=0.0) / positive_profit if positive_profit > 0 else None
                passed = superior >= 0.60 and positive >= 0.60 and max_share is not None and max_share <= 0.40
                daily_status, observed = ("PASS" if passed else "FAIL"), {"superior_days": superior, "positive_days": positive, "max_profit_day_share": max_share}
        recent = context.get("recent_deterioration")
        regimes = context.get("regimes")
        if not isinstance(regimes, (list, tuple)) or not regimes:
            regime_status = "INCONCLUSIVE"
        elif any(not isinstance(item, Mapping) or type(item.get("n")) is not int for item in regimes):
            regime_status = "INCONCLUSIVE"
        elif any(item["n"] < 30 for item in regimes):
            regime_status = "INCONCLUSIVE"
        else:
            regime_status = "FAIL" if any(item.get("trial_loss_significant") is not False for item in regimes) else "PASS"
        return [
            GateResult("DAILY_STABILITY", daily_status, observed, ">=60% superior, >=60% positive, <=40% concentration", "complete-day stability requirements"),
            GateResult("RECENT_STABILITY", "PASS" if recent is False else "FAIL" if recent is True else "INCONCLUSIVE", recent, False, "no clear deterioration in the latest three complete days"),
            GateResult("REGIME_STABILITY", regime_status, regimes, "each regime n>=30 and no significant hidden loss", "regime evidence must be sufficient and non-degrading"),
        ]


__all__ = ["GateResult", "PromotionEvaluation", "PromotionGateEvaluator"]
