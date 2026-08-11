import asyncio
import math
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from nexus_trade.metrics import MetricIntegrityError, calculate_lane_metrics
from nexus_trade.reports import ImmutableReportError, ReportService
from nexus_trade.scheduler import BrasiliaSchedule, DurableReportScheduler


def settlement(result, stake, payout, profit, *, epoch):
    return {
        "result": result,
        "stake": stake,
        "payout": payout,
        "profit": profit,
        "settled": True,
        "contract_id": epoch,
        "decision_epoch": epoch,
    }


class LaneMetricsTests(unittest.TestCase):
    def test_aggregates_raw_money_and_counts_before_deriving_rates(self):
        rows = [
            settlement("won", 0.35, 0.63, 0.28, epoch=1),
            settlement("lost", 0.35, 0.00, -0.35, epoch=2),
            settlement("tie", 0.35, 0.35, 0.00, epoch=3),
            settlement("won", 0.70, 1.19, 0.49, epoch=4),
        ]

        metrics = calculate_lane_metrics(rows)

        self.assertEqual((metrics.n_total, metrics.n_decisive), (4, 3))
        self.assertEqual((metrics.wins, metrics.losses, metrics.ties), (2, 1, 1))
        self.assertAlmostEqual(metrics.accuracy, 2 / 3)
        self.assertAlmostEqual(metrics.capital_at_risk, 1.75)
        self.assertAlmostEqual(metrics.total_profit, 0.42)
        self.assertAlmostEqual(metrics.normalized_expectancy, 0.24)
        self.assertAlmostEqual(metrics.profit_factor, 0.77 / 0.35)
        self.assertAlmostEqual(metrics.max_drawdown, 0.35)
        self.assertAlmostEqual(metrics.recovery_factor, 0.42 / 0.35)
        self.assertEqual(metrics.max_loss_streak, 1)

    def test_combining_raw_sums_does_not_average_daily_percentages(self):
        day_one = [settlement("won", 0.35, 0.70, 0.35, epoch=1)]
        day_two = [
            settlement("lost", 0.35, 0.00, -0.35, epoch=index)
            for index in range(2, 11)
        ]

        metrics = calculate_lane_metrics(day_one + day_two)

        self.assertAlmostEqual(metrics.accuracy, 0.1)
        self.assertAlmostEqual(metrics.normalized_expectancy, -2.8 / 3.5)

    def test_worst_rolling_fifty_uses_ordered_realized_profit(self):
        rows = [
            settlement("won", 0.35, 0.70, 0.35, epoch=index)
            for index in range(50)
        ] + [
            settlement("lost", 0.35, 0.00, -0.35, epoch=index)
            for index in range(50, 100)
        ]

        metrics = calculate_lane_metrics(rows)

        self.assertAlmostEqual(metrics.worst_rolling_50, -17.5)
        self.assertAlmostEqual(metrics.worst_rolling_50_normalized, -1.0)
        self.assertEqual(metrics.max_loss_streak, 50)

    def test_empty_and_zero_loss_samples_use_none_instead_of_fake_infinity(self):
        empty = calculate_lane_metrics([])
        no_losses = calculate_lane_metrics(
            [settlement("won", 0.35, 0.70, 0.35, epoch=1)]
        )

        self.assertIsNone(empty.accuracy)
        self.assertIsNone(empty.normalized_expectancy)
        self.assertIsNone(empty.profit_factor)
        self.assertIsNone(empty.recovery_factor)
        self.assertIsNone(empty.worst_rolling_50)
        self.assertIsNone(no_losses.profit_factor)
        self.assertIsNone(no_losses.recovery_factor)

    def test_invalid_or_non_finite_settlements_fail_closed(self):
        invalid_rows = (
            settlement("won", 0.0, 0.70, 0.35, epoch=1),
            settlement("lost", 0.35, -0.01, -0.35, epoch=1),
            settlement("won", 0.35, 0.70, math.nan, epoch=1),
            settlement("won", 0.35, math.inf, 0.35, epoch=1),
            settlement("unknown", 0.35, 0.70, 0.35, epoch=1),
        )

        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(MetricIntegrityError):
                    calculate_lane_metrics([row])


class ReportSchedulerTests(unittest.TestCase):
    def test_daily_and_monday_boundaries_are_exact_utc_instants(self):
        schedule = BrasiliaSchedule()
        before = datetime(2026, 8, 10, 12, 59, 59, tzinfo=timezone.utc)
        exact = datetime(2026, 8, 10, 13, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            schedule.latest_daily_close(before),
            datetime(2026, 8, 9, 13, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(schedule.latest_daily_close(exact), exact)
        weekly = schedule.weekly_window(exact)
        self.assertEqual(weekly.start_utc, datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(weekly.end_utc, exact)
        self.assertEqual(weekly.end_local, "2026-08-10T10:00:00-03:00")

    def test_missed_jobs_survive_restart_and_concurrent_workers_close_once(self):
        class Service:
            def close_daily(self, window):
                return {"id": "daily-" + window.end_utc.isoformat()}

            def close_weekly(self, window):
                return {"id": "weekly-" + window.end_utc.isoformat()}

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "schedule.db")
            since = datetime(2026, 8, 7, 13, 0, tzinfo=timezone.utc)
            now = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)

            def run_worker():
                return DurableReportScheduler(path, Service()).run_due(since, now=now)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run_worker(), range(2)))

            db = sqlite3.connect(path)
            try:
                jobs = db.execute(
                    "SELECT job_type, status FROM nexus_report_jobs ORDER BY job_type, window_end_utc"
                ).fetchall()
            finally:
                db.close()
            self.assertEqual(len(jobs), 4)
            self.assertTrue(all(status == "COMPLETED" for _, status in jobs))
            self.assertEqual(sum(len(result) for result in results), 4)

            restarted = DurableReportScheduler(path, Service())
            self.assertEqual(restarted.run_due(since, now=now), [])


def report_evidence(*, campaign_id="trial-a", trial_count=300, accumulated=None):
    accumulated = trial_count if accumulated is None else accumulated
    champion = [
        settlement("won" if index % 2 == 0 else "lost", 0.35, 0.70 if index % 2 == 0 else 0.0,
                   0.35 if index % 2 == 0 else -0.35, epoch=10_000 + index)
        for index in range(trial_count)
    ]
    trial = [
        settlement("won" if index % 3 else "lost", 0.35, 0.70 if index % 3 else 0.0,
                   0.35 if index % 3 else -0.35, epoch=20_000 + index)
        for index in range(trial_count)
    ]
    provenance = {
        "symbol": "R_100", "timeframe_seconds": 60, "duration_seconds": 58,
        "provenance_hash": "a" * 64,
    }
    return {
        "campaign_id": campaign_id,
        "champion": {
            "version_id": "champion-v1", "version_hash": "b" * 64,
            "configuration": {"bollinger": {"period": 20}},
            "feature_schema": ["bollinger_percent_b"], "entry_rules": ["bollinger"],
            "model": "deterministic", "settlements": champion, **provenance,
        },
        "trial": {
            "version_id": "trial-v2", "version_hash": "c" * 64,
            "configuration": {"bollinger": {"period": 20}, "adx": {"period": 14}},
            "feature_schema": ["bollinger_percent_b", "adx"],
            "entry_rules": ["bollinger", "adx_gate"], "model": "hgb-v1",
            "settlements": trial, **provenance,
        },
        "complete_days": 7,
        "trial_accumulated_operations": accumulated,
        "daily": [{"date": f"2026-08-{day:02d}", "champion": {}, "trial": {}} for day in range(3, 10)],
        "gates": [],
        "recommendation": "INCONCLUSIVE",
        "audit": [{"action": "REPORT_CLOSE", "actor": "system"}],
    }


class ImmutableReportTests(unittest.TestCase):
    def test_snapshot_is_content_addressed_immutable_and_historically_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            service = ReportService(path)
            window = BrasiliaSchedule().weekly_window(
                datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
            )
            report = service.close_weekly(window, report_evidence())
            same = service.close_weekly(window, report_evidence())

            self.assertEqual(report.report_hash, same.report_hash)
            self.assertEqual(report.id, "report-" + report.report_hash[:24])
            self.assertEqual(report.snapshot["window"]["end_local"], "2026-08-10T10:00:00-03:00")
            self.assertEqual(report.snapshot["accumulated_progress"]["operations"], 300)
            self.assertIn("configuration", report.snapshot["diffs"])
            self.assertIn("indicators", report.snapshot["diffs"])
            self.assertIn("features", report.snapshot["diffs"])
            self.assertIn("entry_rules", report.snapshot["diffs"])
            self.assertIn("model", report.snapshot["diffs"])
            self.assertEqual(service.get_weekly("2026-08-10").report_hash, report.report_hash)

            changed = report_evidence()
            changed["audit"] = [{"action": "DIFFERENT_CLOSE", "actor": "system"}]
            with self.assertRaises(ImmutableReportError):
                service.close_weekly(window, changed)
            db = sqlite3.connect(path)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute("UPDATE nexus_reports SET snapshot='{}' WHERE id=?", (report.id,))
            finally:
                db.close()

    def test_weekly_visual_totals_reset_but_campaign_accumulates_and_replacement_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ReportService(os.path.join(directory, "reports.db"))
            schedule = BrasiliaSchedule()
            first_window = schedule.weekly_window(datetime(2026, 8, 10, 13, tzinfo=timezone.utc))
            second_window = schedule.weekly_window(datetime(2026, 8, 17, 13, tzinfo=timezone.utc))
            first = service.close_weekly(first_window, report_evidence(trial_count=200, accumulated=200))
            second = service.close_weekly(second_window, report_evidence(trial_count=120, accumulated=320))

            self.assertEqual(first.snapshot["trial"]["metrics"]["n_total"], 200)
            self.assertEqual(second.snapshot["trial"]["metrics"]["n_total"], 120)
            self.assertEqual(second.snapshot["accumulated_progress"]["operations"], 320)
            self.assertTrue(second.snapshot["accumulated_progress"]["eligible_count"])

            third_window = schedule.weekly_window(datetime(2026, 8, 24, 13, tzinfo=timezone.utc))
            trial_b = service.close_weekly(
                third_window,
                report_evidence(campaign_id="trial-b", trial_count=0, accumulated=0),
            )
            self.assertEqual(trial_b.snapshot["campaign_id"], "trial-b")
            self.assertEqual(trial_b.snapshot["accumulated_progress"]["operations"], 0)
            self.assertEqual(service.get_weekly("2026-08-10").report_hash, first.report_hash)

    def test_persisted_close_assigns_late_settlement_by_decision_time_and_excludes_end_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            os.environ.setdefault("DERIV_APP_ID", "dummy-app")
            os.environ.setdefault("DERIV_API_TOKEN", "dummy-token")
            os.environ.setdefault("DASHBOARD_API_KEY", "dummy-dashboard")
            os.environ.setdefault("INTERNAL_API_TOKEN", "dummy-internal")
            from database.repository import DatabaseRepository

            asyncio.run(DatabaseRepository(path).init_db())
            window = BrasiliaSchedule().weekly_window(
                datetime(2026, 8, 10, 13, tzinfo=timezone.utc)
            )
            inside = int(datetime(2026, 8, 5, 13, tzinfo=timezone.utc).timestamp())
            exact_end = int(window.end_utc.timestamp())
            db = sqlite3.connect(path)
            try:
                version_id = db.execute("SELECT champion_version_id FROM nexus_runtime").fetchone()[0]
                campaign_id = db.execute("SELECT id FROM nexus_campaigns WHERE lane='challenger_trial'").fetchone()[0]
                db.execute("UPDATE nexus_campaigns SET started_at='2026-08-03 13:00:00' WHERE id=?", (campaign_id,))
                for lane, suffix, epoch, contract_id in (
                    ("champion_baseline", "champion", inside, 7001),
                    ("challenger_trial", "trial-late", inside, 7002),
                    ("challenger_trial", "trial-boundary", exact_end, 7003),
                ):
                    decision_id = "report-decision-" + suffix
                    db.execute(
                        """INSERT INTO nexus_decisions
                           (id, lane, nexus_version_id, campaign_id, symbol, signal_epoch, payload)
                           VALUES (?, ?, ?, ?, 'R_100', ?, '{}')""",
                        (decision_id, lane, version_id, campaign_id if lane == "challenger_trial" else None, epoch),
                    )
                    db.execute(
                        """INSERT INTO trades
                           (bot_id, symbol, contract_id, stake, payout, profit, result, status,
                            purchase_time, lane, nexus_version_id, campaign_id, decision_id)
                           VALUES ('nexus-trade', 'R_100', ?, 0.35, 0.70, 0.35, 'won', 'closed',
                                   ?, ?, ?, ?, ?)""",
                        (contract_id, exact_end + 3600, lane, version_id,
                         campaign_id if lane == "challenger_trial" else None, decision_id),
                    )
                db.commit()
            finally:
                db.close()

            report = ReportService(path).close_weekly(window)

            self.assertEqual(report.snapshot["champion"]["metrics"]["n_total"], 1)
            self.assertEqual(report.snapshot["trial"]["metrics"]["n_total"], 1)
            self.assertEqual(report.snapshot["accumulated_progress"]["operations"], 1)


if __name__ == "__main__":
    unittest.main()
