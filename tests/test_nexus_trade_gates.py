import math
import unittest

from nexus_trade.gates import PromotionGateEvaluator
from nexus_trade.metrics import calculate_lane_metrics


def rows(profits, *, first_epoch=1):
    result = []
    for index, profit in enumerate(profits):
        won = profit > 0
        result.append(
            {
                "result": "won" if won else "lost",
                "stake": 0.35,
                "payout": 0.35 + profit if won else 0.0,
                "profit": profit,
                "settled": True,
                "contract_id": first_epoch + index,
                "decision_epoch": first_epoch + index,
            }
        )
    return result


def passing_context():
    daily = [
        {"champion_expectancy": 0.04, "trial_expectancy": 0.10, "trial_profit": 1.0}
        for _ in range(7)
    ]
    return {
        "complete_days": 7,
        "trial_settled_operations": 350,
        "integrity": {
            "trial_frozen": True,
            "all_reconciled": True,
            "no_duplicates": True,
            "no_future_leakage": True,
            "dispatch_within_limit": True,
            "candle_coverage": 1.0,
            "reproducible": True,
            "risk_limits_ok": True,
        },
        "champion_provenance": {
            "symbol": "R_100", "timeframe_seconds": 60, "duration_seconds": 58,
            "window_start": 1, "window_end": 8, "campaign_id": "champion-a",
            "version_id": "champion-v1", "provenance_hash": "a" * 64,
        },
        "trial_provenance": {
            "symbol": "R_100", "timeframe_seconds": 60, "duration_seconds": 58,
            "window_start": 1, "window_end": 8, "campaign_id": "trial-a",
            "version_id": "trial-v2", "provenance_hash": "a" * 64,
        },
        "temporal_blocks": [
            {"champion": -0.01, "trial": 0.08 + index / 1000}
            for index in range(14)
        ],
        "daily": daily,
        "recent_deterioration": False,
        "regimes": [{"n": 30, "trial_loss_significant": False}],
        "dsr_probability": 0.97,
        "pbo": 0.08,
        "sensitivity_passed": True,
        "change_families": ["indicator_reconfiguration"],
        "bollinger_present": True,
        "new_indicator_ablation_passed": True,
    }


class PromotionGateTests(unittest.TestCase):
    def setUp(self):
        self.champion = calculate_lane_metrics(rows([0.14] * 210 + [-0.35] * 140))
        self.trial = calculate_lane_metrics(rows([0.21] * 250 + [-0.35] * 100, first_epoch=1000))
        self.evaluator = PromotionGateEvaluator(seed=11, bootstrap_samples=1000)

    def test_all_conservative_gates_pass_only_with_paired_temporal_evidence(self):
        evaluation = self.evaluator.evaluate(self.champion, self.trial, passing_context())

        self.assertEqual(evaluation.recommendation, "EVOLVE")
        self.assertTrue(evaluation.gates)
        self.assertEqual({gate.status for gate in evaluation.gates}, {"PASS"})
        bootstrap = next(g for g in evaluation.gates if g.code == "BLOCK_BOOTSTRAP_95")
        self.assertGreater(bootstrap.observed["lower_bound"], 0)
        self.assertGreaterEqual(bootstrap.observed["probability_superior"], 0.95)

    def test_sample_integrity_and_comparability_fail_closed_first(self):
        context = passing_context()
        context["complete_days"] = 6
        result = self.evaluator.evaluate(self.champion, self.trial, context)
        self.assertEqual(result.recommendation, "INCONCLUSIVE")
        self.assertEqual(result.gates[0].code, "MINIMUM_SAMPLE")
        self.assertEqual(result.gates[0].status, "INCONCLUSIVE")

        context = passing_context()
        context["trial_provenance"]["window_end"] = 9
        result = self.evaluator.evaluate(self.champion, self.trial, context)
        comparison = next(g for g in result.gates if g.code == "COMPARABLE_PROVENANCE")
        self.assertEqual(comparison.status, "INCONCLUSIVE")
        self.assertIn("window", comparison.reason.lower())

    def test_performance_thresholds_are_normalized_and_accuracy_is_not_a_gate(self):
        context = passing_context()
        weak = calculate_lane_metrics(rows([0.15] * 190 + [-0.35] * 160, first_epoch=2000))
        result = self.evaluator.evaluate(self.champion, weak, context)

        self.assertEqual(result.recommendation, "REANALYZE")
        materiality = next(g for g in result.gates if g.code == "EXPECTANCY_IMPROVEMENT")
        self.assertEqual(materiality.status, "FAIL")
        self.assertAlmostEqual(materiality.threshold, 0.02)
        self.assertFalse(any(g.code == "ACCURACY" for g in result.gates))

    def test_nonfinite_selection_evidence_and_multiple_change_families_do_not_pass(self):
        context = passing_context()
        context["dsr_probability"] = math.nan
        context["change_families"] = ["model", "entry_rule"]
        result = self.evaluator.evaluate(self.champion, self.trial, context)

        self.assertEqual(next(g for g in result.gates if g.code == "DSR").status, "INCONCLUSIVE")
        self.assertEqual(next(g for g in result.gates if g.code == "CHANGE_BUDGET").status, "FAIL")
        self.assertNotEqual(result.recommendation, "EVOLVE")

    def test_missing_temporal_blocks_never_falls_back_to_independent_trade_bootstrap(self):
        context = passing_context()
        context["temporal_blocks"] = []
        result = self.evaluator.evaluate(self.champion, self.trial, context)

        gate = next(g for g in result.gates if g.code == "BLOCK_BOOTSTRAP_95")
        self.assertEqual(gate.status, "INCONCLUSIVE")
        self.assertIn("temporal", gate.reason.lower())

    def test_indicator_addition_without_explicit_ablation_evidence_never_passes(self):
        context = passing_context()
        context["change_families"] = ["indicator_addition"]
        context.pop("new_indicator_ablation_passed")

        result = self.evaluator.evaluate(self.champion, self.trial, context)

        gate = next(g for g in result.gates if g.code == "CHANGE_BUDGET")
        self.assertIn(gate.status, {"FAIL", "INCONCLUSIVE"})
        self.assertNotEqual(result.recommendation, "EVOLVE")


if __name__ == "__main__":
    unittest.main()
