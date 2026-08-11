"""Brasilia report windows and durable, restart-safe close jobs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo


UTC = timezone.utc
BRASILIA = ZoneInfo("America/Sao_Paulo")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("an aware datetime is required")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ReportWindow:
    kind: str
    start_utc: datetime
    end_utc: datetime
    start_local: str
    end_local: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "start_local": self.start_local,
            "end_local": self.end_local,
        }


class BrasiliaSchedule:
    timezone = BRASILIA

    @staticmethod
    def _boundary(local_date: date) -> datetime:
        return datetime.combine(local_date, time(10, 0), BRASILIA).astimezone(UTC)

    def latest_daily_close(self, instant: datetime) -> datetime:
        local = _utc(instant).astimezone(BRASILIA)
        boundary_date = local.date() if local.timetz().replace(tzinfo=None) >= time(10) else local.date() - timedelta(days=1)
        return self._boundary(boundary_date)

    def daily_window(self, close: datetime) -> ReportWindow:
        close_utc = _utc(close)
        local = close_utc.astimezone(BRASILIA)
        if local.time().replace(tzinfo=None) != time(10):
            raise ValueError("daily close must be exactly 10:00 America/Sao_Paulo")
        start = self._boundary(local.date() - timedelta(days=1))
        return self._window("daily", start, close_utc)

    def weekly_window(self, close: datetime) -> ReportWindow:
        close_utc = _utc(close)
        local = close_utc.astimezone(BRASILIA)
        if local.weekday() != 0 or local.time().replace(tzinfo=None) != time(10):
            raise ValueError("weekly close must be Monday 10:00 America/Sao_Paulo")
        start = self._boundary(local.date() - timedelta(days=7))
        return self._window("weekly", start, close_utc)

    @staticmethod
    def _window(kind: str, start: datetime, end: datetime) -> ReportWindow:
        return ReportWindow(
            kind=kind,
            start_utc=start,
            end_utc=end,
            start_local=start.astimezone(BRASILIA).isoformat(),
            end_local=end.astimezone(BRASILIA).isoformat(),
        )

    def due_windows(self, since_exclusive: datetime, now: datetime) -> tuple[ReportWindow, ...]:
        since = _utc(since_exclusive)
        end = _utc(now)
        if end < since:
            raise ValueError("now cannot precede the durable scheduler cursor")
        first_date = since.astimezone(BRASILIA).date()
        final_date = end.astimezone(BRASILIA).date()
        windows: list[ReportWindow] = []
        current = first_date
        while current <= final_date:
            boundary = self._boundary(current)
            if since < boundary <= end:
                windows.append(self.daily_window(boundary))
                if current.weekday() == 0:
                    windows.append(self.weekly_window(boundary))
            current += timedelta(days=1)
        return tuple(sorted(windows, key=lambda window: (window.end_utc, window.kind)))


class DurableReportScheduler:
    """Claim close boundaries transactionally; completed jobs are never repeated."""

    def __init__(
        self,
        db_path: str,
        report_service,
        *,
        clock: Callable[[], datetime] | None = None,
        reclaim_after_seconds: int = 300,
    ):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        self.report_service = report_service
        self.clock = clock or (lambda: datetime.now(UTC))
        self.reclaim_after_seconds = reclaim_after_seconds
        self.schedule = BrasiliaSchedule()
        self.worker_id = uuid.uuid4().hex
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS nexus_report_jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL CHECK (job_type IN ('daily', 'weekly')),
                    window_start_utc TEXT NOT NULL,
                    window_end_utc TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED')),
                    owner_id TEXT NOT NULL,
                    claimed_at_utc TEXT NOT NULL,
                    result_json TEXT,
                    UNIQUE(job_type, window_end_utc)
                );
                """
            )

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            yield db
        finally:
            db.close()

    def run_due(
        self,
        since_exclusive: datetime,
        *,
        now: datetime | None = None,
    ) -> list[dict]:
        current = _utc(now or self.clock())
        completed: list[dict] = []
        for window in self.schedule.due_windows(since_exclusive, current):
            job_id = f"{window.kind}:{window.end_utc.isoformat()}"
            if not self._claim(job_id, window, current):
                continue
            method = getattr(self.report_service, f"close_{window.kind}")
            try:
                result = method(window)
                plain = result if isinstance(result, dict) else {"id": getattr(result, "id", job_id)}
                encoded = json.dumps(plain, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
                with self._connection() as db:
                    db.execute(
                        "UPDATE nexus_report_jobs SET status='COMPLETED', result_json=? WHERE id=? AND owner_id=?",
                        (encoded, job_id, self.worker_id),
                    )
                    db.commit()
                completed.append(plain)
            except BaseException:
                # RUNNING remains durable and is reclaimable only after its lease expires.
                raise
        return completed

    def _claim(self, job_id: str, window: ReportWindow, now: datetime) -> bool:
        claimed_at = now.isoformat()
        stale_before = (now - timedelta(seconds=self.reclaim_after_seconds)).isoformat()
        with self._connection() as db:
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                INSERT INTO nexus_report_jobs (
                    id, job_type, window_start_utc, window_end_utc,
                    status, owner_id, claimed_at_utc
                ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)
                ON CONFLICT(job_type, window_end_utc) DO UPDATE SET
                    id=excluded.id, owner_id=excluded.owner_id,
                    claimed_at_utc=excluded.claimed_at_utc, status='RUNNING'
                WHERE nexus_report_jobs.status='RUNNING'
                  AND nexus_report_jobs.claimed_at_utc < ?
                """,
                (
                    job_id, window.kind, window.start_utc.isoformat(),
                    window.end_utc.isoformat(), self.worker_id, claimed_at, stale_before,
                ),
            )
            claimed = cursor.rowcount == 1
            db.commit()
            return claimed


__all__ = ["BrasiliaSchedule", "DurableReportScheduler", "ReportWindow"]
