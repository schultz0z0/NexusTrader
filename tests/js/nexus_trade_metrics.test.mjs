import test from "node:test";
import assert from "node:assert/strict";

import {
  buildReportPresentation,
  formatAccuracy,
  formatMoney,
  normalizeReportLocation,
  shiftAlignedWeek,
} from "../../static/js/nexus_trade_metrics.js";

const metrics = (overrides = {}) => ({
  n_total: 300,
  n_decisive: 300,
  wins: 174,
  losses: 126,
  ties: 0,
  accuracy: 0.58,
  capital_at_risk: 105,
  total_stake: 105,
  total_payout: 166.2,
  total_profit: 8.4,
  normalized_expectancy: 0.08,
  average_payout: 1.583,
  gross_profit: 61.2,
  gross_loss: 52.8,
  profit_factor: 1.159,
  max_drawdown: 4.2,
  max_drawdown_normalized: 0.04,
  recovery_factor: 2,
  worst_rolling_50: -1.05,
  worst_rolling_50_normalized: -0.06,
  max_loss_streak: 5,
  ...overrides,
});

const weeklyReport = () => ({
  id: "report-week-1",
  report_hash: "a".repeat(64),
  snapshot: {
    report_type: "weekly",
    campaign_id: "campaign-trial-a",
    window: {
      start_local: "2026-08-03T10:00:00-03:00",
      end_local: "2026-08-10T10:00:00-03:00",
    },
    champion: { version_id: "champion-v1" },
    trial: { version_id: "trial-v2" },
    days: [
      { date: "2026-08-03", champion: metrics({ n_total: 20, n_decisive: 20, wins: 18, losses: 2, accuracy: 0.9 }), trial: metrics({ n_total: 22, n_decisive: 22, wins: 11, losses: 11, accuracy: 0.5 }) },
      { date: "2026-08-04", champion: metrics({ n_total: 20, n_decisive: 20, wins: 2, losses: 18, accuracy: 0.1 }), trial: metrics({ n_total: 24, n_decisive: 24, wins: 14, losses: 10, accuracy: 14 / 24 }) },
    ],
    full_totals: {
      champion: metrics(),
      trial: metrics({ wins: 180, losses: 120, accuracy: 0.6, total_profit: 12.6 }),
    },
    accumulated_progress: {
      operations: 327,
      target: 300,
      complete_days: 8,
      required_days: 7,
      eligible_count: true,
      eligible_days: true,
    },
    gates: [
      { code: "SAMPLE", status: "PASS", observed: 327, threshold: 300, reason: "amostra suficiente" },
      { code: "PBO", status: "INCONCLUSIVE", observed: null, threshold: 0.25, reason: "evidência pendente" },
    ],
    recommendation: "INCONCLUSIVE",
    recommendation_reasons: ["evidência pendente"],
  },
});

test("money and accuracy use Brazilian display without averaging daily percentages", () => {
  assert.equal(formatMoney(1.05), "US$ 1,05");
  assert.equal(formatAccuracy({ wins: 174, losses: 126 }), "174/300 (58,00%)");

  const view = buildReportPresentation(weeklyReport());
  assert.equal(view.weekTotal.champion.accuracy, "174/300 (58,00%)");
  assert.equal(view.weekTotal.trial.accuracy, "180/300 (60,00%)");
  assert.equal(view.days[0].champion.accuracy, "18/20 (90,00%)");
  assert.equal(view.days[1].champion.accuracy, "2/20 (10,00%)");
});

test("presentation preserves mandatory governed metrics and accumulated eligibility", () => {
  const view = buildReportPresentation(weeklyReport());

  assert.equal(view.id, "report-week-1");
  assert.equal(view.week, "2026-08-10");
  assert.equal(view.progress.label, "327/300 operações · 8/7 dias");
  assert.equal(view.progress.eligible, true);
  assert.equal(view.weekTotal.trial.profit, "US$ 12,60");
  assert.equal(view.weekTotal.champion.profitFactor, "1,159");
  assert.equal(view.weekTotal.champion.drawdown, "US$ 4,20");
  assert.equal(view.weekTotal.champion.worstBlock, "-US$ 1,05");
  assert.equal(view.weekTotal.champion.lossStreak, "5");
  assert.deepEqual(view.gates.map((gate) => gate.status), ["PASS", "INCONCLUSIVE"]);
  assert.equal(view.recommendation, "INCONCLUSIVE");
});

test("week navigation only accepts Monday report boundaries and keeps deep-link state", () => {
  assert.equal(shiftAlignedWeek("2026-08-10", -1), "2026-08-03");
  assert.equal(shiftAlignedWeek("2026-08-10", 1), "2026-08-17");
  assert.equal(shiftAlignedWeek("2026-08-11", -1), null);

  assert.deepEqual(
    normalizeReportLocation("?nexus_tab=evolution&nexus_week=2026-08-10"),
    { tab: "evolution", week: "2026-08-10" },
  );
  assert.deepEqual(
    normalizeReportLocation("?nexus_tab=unknown&nexus_week=2026-08-11"),
    { tab: "operations", week: null },
  );
});
