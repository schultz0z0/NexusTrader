"""Append-only on-disk archive for the NexusTrade R_100 tick stream."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Mapping

from nexus_trade.constants import NEXUS_SYMBOL


class TickArchive:
    """Store received R_100 ticks in immutable daily gzip JSONL segments.

    A currently open segment has a ``.partial`` suffix.  Each append writes a
    complete gzip member, so a process can recover every fully written line on
    restart.  Closing atomically renames that file before recording its manifest.
    """

    def __init__(self, root_path: str | Path, db_path: str | Path, symbol: str = NEXUS_SYMBOL):
        self.root_path = Path(root_path)
        self.db_path = Path(db_path)
        self.symbol = symbol
        self._partial_path: Path | None = None
        self._day: tuple[int, int, int] | None = None
        self._ticks: list[dict] = []
        self._fingerprints: set[str] = set()
        self._last_epoch: int | None = None
        self._ensure_manifest_table()
        self._recover_published_segments()
        self._recover_partial_segment()

    def append(self, tick: Mapping[str, object]) -> None:
        """Append one tick, rejecting a timestamp that would reverse the stream."""
        normalized = dict(tick)
        if "epoch" not in normalized or "quote" not in normalized:
            raise ValueError("tick must contain epoch and quote")
        if normalized.get("symbol", self.symbol) != self.symbol:
            raise ValueError(f"TickArchive only accepts {self.symbol} ticks")
        epoch = int(normalized["epoch"])
        if self._last_epoch is not None and epoch < self._last_epoch:
            raise ValueError("ticks must be appended in nondecreasing epoch order")
        fingerprint = self._fingerprint(normalized)
        if fingerprint in self._fingerprints:
            return

        day = self._utc_day(epoch)
        if self._partial_path is not None and day != self._day:
            self.close_segment()
        if self._partial_path is None:
            self._open_segment(day)

        line = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n"
        with gzip.open(self._partial_path, "ab") as stream:
            stream.write(line.encode("utf-8"))
        self._ticks.append(normalized)
        self._fingerprints.add(fingerprint)
        self._last_epoch = epoch

    def close_segment(self) -> dict | None:
        """Atomically publish the active gzip segment and record its SQLite manifest."""
        if self._partial_path is None:
            return None
        if not self._ticks:
            self._partial_path.unlink(missing_ok=True)
            self._clear_active_segment()
            return None

        partial_path = self._partial_path
        final_path = partial_path.with_suffix("")
        os.replace(partial_path, final_path)
        manifest = self._record_segment(final_path, self._ticks)
        self._clear_active_segment()
        return manifest

    def _record_segment(self, path: Path, ticks: list[dict]) -> dict:
        """Insert a manifest idempotently, including after a rename/DB crash gap."""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "id": str(uuid.uuid4()),
            "symbol": self.symbol,
            "start_epoch": int(ticks[0]["epoch"]),
            "end_epoch": int(ticks[-1]["epoch"]),
            "tick_count": len(ticks),
            "byte_count": path.stat().st_size,
            "sha256": digest,
            "path": str(path),
        }
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO nexus_tick_segments
                    (id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path)
                VALUES (:id, :symbol, :start_epoch, :end_epoch, :tick_count, :byte_count, :sha256, :path)
                """,
                manifest,
            )
            db.commit()
        finally:
            db.close()
        return manifest

    def replay(self, start: int, end: int) -> Iterator[dict]:
        """Yield an inclusive, append-ordered prefix of archived ticks.

        The archive refuses reverse-ordered appends, so concatenating segments in
        path order neither sorts nor introduces future information.
        """
        if int(end) < int(start):
            raise ValueError("end must not precede start")
        segment_paths = sorted(self.root_path.glob(f"{self.symbol}/**/*.jsonl.gz"))
        if self._partial_path is not None:
            segment_paths.append(self._partial_path)
        for path in segment_paths:
            for tick in self._read_ticks(path):
                epoch = int(tick["epoch"])
                if int(start) <= epoch <= int(end):
                    yield tick

    def _ensure_manifest_table(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_tick_segments (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    start_epoch INTEGER NOT NULL,
                    end_epoch INTEGER NOT NULL,
                    tick_count INTEGER NOT NULL,
                    byte_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            db.commit()
        finally:
            db.close()

    def _recover_partial_segment(self) -> None:
        partials = sorted(self.root_path.glob(f"{self.symbol}/**/*.jsonl.gz.partial"))
        if not partials:
            return
        # At most one writer is supported.  Older partials are complete enough to
        # publish first, preserving the original append order across a restart.
        for stale in partials[:-1]:
            self._load_partial(stale)
            self.close_segment()
        self._load_partial(partials[-1])

    def _recover_published_segments(self) -> None:
        for path in sorted(self.root_path.glob(f"{self.symbol}/**/*.jsonl.gz")):
            ticks = list(self._read_ticks(path))
            if not ticks:
                raise ValueError(f"published segment is empty: {path}")
            epochs = [int(tick["epoch"]) for tick in ticks]
            if any(right < left for left, right in zip(epochs, epochs[1:])):
                raise ValueError(f"published segment is not append ordered: {path}")
            self._record_segment(path, ticks)

    def _load_partial(self, path: Path) -> None:
        ticks = list(self._read_ticks(path))
        if not ticks:
            path.unlink(missing_ok=True)
            return
        epochs = [int(tick["epoch"]) for tick in ticks]
        if any(right < left for left, right in zip(epochs, epochs[1:])):
            raise ValueError(f"partial segment is not append ordered: {path}")
        self._partial_path = path
        self._ticks = ticks
        self._fingerprints = {self._fingerprint(tick) for tick in ticks}
        self._last_epoch = epochs[-1]
        self._day = self._utc_day(epochs[0])

    def _open_segment(self, day: tuple[int, int, int]) -> None:
        year, month, date = day
        directory = self.root_path / self.symbol / f"{year:04d}" / f"{month:02d}" / f"{date:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        self._partial_path = directory / f"{uuid.uuid4().hex}.jsonl.gz.partial"
        self._day = day
        self._ticks = []
        self._fingerprints = set()

    def _clear_active_segment(self) -> None:
        self._partial_path = None
        self._day = None
        self._ticks = []
        self._fingerprints = set()

    @staticmethod
    def _read_ticks(path: Path) -> Iterator[dict]:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)

    @staticmethod
    def _fingerprint(tick: Mapping[str, object]) -> str:
        return json.dumps(dict(tick), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _utc_day(epoch: int) -> tuple[int, int, int]:
        date = datetime.fromtimestamp(epoch, tz=UTC)
        return date.year, date.month, date.day
