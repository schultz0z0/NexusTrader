"""Durable, causal archive for the single supported NexusTrade tick stream."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Mapping

from nexus_trade.constants import NEXUS_SYMBOL


class TickArchive:
    """Append normalized R_100 ticks to ordered gzip JSONL segments.

    ``segment_sequence`` in the SQLite manifest is the durable ordering source;
    filenames are deliberately not used to order replay.  Each append is a complete
    gzip member that is flushed and fsynced before it becomes recoverable.
    """

    def __init__(self, root_path: str | Path, db_path: str | Path, symbol: str = NEXUS_SYMBOL):
        if symbol != NEXUS_SYMBOL:
            raise ValueError(f"TickArchive only supports {NEXUS_SYMBOL}")
        self.root_path = Path(root_path)
        self.db_path = Path(db_path)
        self.symbol = NEXUS_SYMBOL
        self._partial_path: Path | None = None
        self._day: tuple[int, int, int] | None = None
        self._ticks: list[dict] = []
        self._fingerprints: set[str] = set()
        self._last_epoch: int | None = None
        self._tail_fingerprints: set[str] = set()
        self._next_sequence = 1
        self._ensure_manifest_table()
        self._recover_published_segments()
        self._recover_partial_segment()

    def append(self, tick: Mapping[str, object]) -> None:
        """Append one validated tick without allowing causal backdating."""
        normalized = self._normalize_tick(tick)
        epoch = normalized["epoch"]
        fingerprint = self._fingerprint(normalized)
        if self._last_epoch is not None:
            if epoch < self._last_epoch:
                raise ValueError("ticks must be appended in nondecreasing epoch order")
            if epoch == self._last_epoch and fingerprint in self._tail_fingerprints:
                return
        if fingerprint in self._fingerprints:
            return

        day = self._utc_day(epoch)
        if self._partial_path is not None and day != self._day:
            self.close_segment()
        if self._partial_path is None:
            self._open_segment(day)

        self._append_member(self._partial_path, normalized)
        self._ticks.append(normalized)
        self._fingerprints.add(fingerprint)
        self._advance_tail(normalized)

    def close_segment(self) -> dict | None:
        """Publish the active segment atomically, then attest it in SQLite."""
        if self._partial_path is None:
            return None
        if not self._ticks:
            self._partial_path.unlink(missing_ok=True)
            self._clear_active_segment()
            return None
        partial_path = self._partial_path
        final_path = partial_path.with_suffix("")
        os.replace(partial_path, final_path)
        manifest = self._record_segment(final_path, self._ticks, self._next_sequence)
        self._next_sequence = manifest["segment_sequence"] + 1
        self._clear_active_segment()
        return manifest

    def replay(self, start: int, end: int) -> Iterator[dict]:
        """Yield inclusively bounded ticks in their persisted causal sequence."""
        start_epoch = self._require_epoch(start, "start")
        end_epoch = self._require_epoch(end, "end")
        if end_epoch < start_epoch:
            raise ValueError("end must not precede start")
        last_epoch: int | None = None
        for row in self._manifest_rows():
            path = self._manifest_path(row)
            for tick in self._read_ticks(path):
                epoch = tick["epoch"]
                if last_epoch is not None and epoch < last_epoch:
                    raise ValueError("manifest sequence is not globally causal")
                last_epoch = epoch
                if start_epoch <= epoch <= end_epoch:
                    yield tick
        for tick in self._ticks:
            epoch = tick["epoch"]
            if last_epoch is not None and epoch < last_epoch:
                raise ValueError("partial segment is not globally causal")
            last_epoch = epoch
            if start_epoch <= epoch <= end_epoch:
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
                    segment_sequence INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(nexus_tick_segments)")}
            if "segment_sequence" not in columns:
                db.execute("ALTER TABLE nexus_tick_segments ADD COLUMN segment_sequence INTEGER")
                rows = db.execute(
                    "SELECT id FROM nexus_tick_segments ORDER BY start_epoch, end_epoch, created_at, id"
                ).fetchall()
                for sequence, (segment_id,) in enumerate(rows, start=1):
                    db.execute(
                        "UPDATE nexus_tick_segments SET segment_sequence = ? WHERE id = ?",
                        (sequence, segment_id),
                    )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_tick_segments_symbol_sequence "
                "ON nexus_tick_segments(symbol, segment_sequence)"
            )
            db.commit()
        finally:
            db.close()

    def _recover_published_segments(self) -> None:
        rows = self._manifest_rows()
        registered_paths = set()
        for row in rows:
            path = self._manifest_path(row)
            registered_paths.add(path.resolve())
            ticks = self._read_ticks(path)
            self._record_segment(path, ticks, row["segment_sequence"], row["id"])

        for path in self.root_path.glob(f"{self.symbol}/**/*.jsonl.gz"):
            if path.resolve() in registered_paths:
                continue
            ticks = self._read_ticks(path)
            sequence = self._sequence_from_path(path) or self._next_sequence
            self._record_segment(path, ticks, sequence)
            self._next_sequence = max(self._next_sequence, sequence + 1)

        self._rebuild_global_tail()

    def _recover_partial_segment(self) -> None:
        partials = sorted(self.root_path.glob(f"{self.symbol}/**/*.jsonl.gz.partial"))
        if not partials:
            return
        if len(partials) > 1:
            raise ValueError("multiple active tick segments require operator recovery")
        self._load_partial(partials[0])

    def _rebuild_global_tail(self) -> None:
        self._last_epoch = None
        self._tail_fingerprints = set()
        self._next_sequence = 1
        for row in self._manifest_rows():
            self._next_sequence = max(self._next_sequence, row["segment_sequence"] + 1)
            for tick in self._read_ticks(self._manifest_path(row)):
                self._advance_tail(tick)

    def _load_partial(self, path: Path) -> None:
        ticks = self._read_ticks(path, recover_partial=True)
        if not ticks:
            path.unlink(missing_ok=True)
            return
        for tick in ticks:
            self._validate_tail(tick)
            self._advance_tail(tick)
        self._partial_path = path
        self._ticks = ticks
        self._fingerprints = {self._fingerprint(tick) for tick in ticks}
        self._day = self._utc_day(ticks[0]["epoch"])

    def _record_segment(
        self,
        path: Path,
        ticks: list[dict],
        sequence: int,
        segment_id: str | None = None,
    ) -> dict:
        if not ticks:
            raise ValueError(f"segment is empty: {path}")
        self._validate_segment_ticks(ticks, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "id": segment_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"nexus-trade:{self.symbol}:{digest}")),
            "symbol": self.symbol,
            "start_epoch": ticks[0]["epoch"],
            "end_epoch": ticks[-1]["epoch"],
            "tick_count": len(ticks),
            "byte_count": path.stat().st_size,
            "sha256": digest,
            "path": str(path),
            "segment_sequence": sequence,
        }
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT * FROM nexus_tick_segments WHERE id = ? OR path = ? OR sha256 = ?",
                (manifest["id"], manifest["path"], manifest["sha256"]),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise ValueError("manifest identity conflict")
                existing = dict(rows[0])
                expected = {key: manifest[key] for key in manifest}
                if any(existing.get(key) != value for key, value in expected.items()):
                    raise ValueError("manifest diverges from published segment")
                return existing
            db.execute(
                """
                INSERT INTO nexus_tick_segments
                    (id, symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, segment_sequence)
                VALUES (:id, :symbol, :start_epoch, :end_epoch, :tick_count, :byte_count, :sha256, :path, :segment_sequence)
                """,
                manifest,
            )
            db.commit()
            return manifest
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError("manifest identity conflict") from exc
        finally:
            db.close()

    def _manifest_rows(self) -> list[dict]:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM nexus_tick_segments WHERE symbol = ? ORDER BY segment_sequence",
                (self.symbol,),
            ).fetchall()]
        finally:
            db.close()
        sequences = [row["segment_sequence"] for row in rows]
        if any(type(sequence) is not int or sequence < 1 for sequence in sequences):
            raise ValueError("manifest has invalid segment sequence")
        if sequences != list(range(1, len(rows) + 1)):
            raise ValueError("manifest segment sequence is not contiguous")
        return rows

    def _manifest_path(self, row: Mapping[str, object]) -> Path:
        path = Path(str(row["path"])).resolve()
        root = (self.root_path / self.symbol).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError("manifest path is missing or outside the R_100 archive")
        return path

    def _validate_segment_ticks(self, ticks: list[dict], path: Path) -> None:
        previous: int | None = None
        for tick in ticks:
            normalized = self._normalize_tick(tick)
            if normalized != tick:
                raise ValueError(f"segment contains non-normalized tick: {path}")
            if previous is not None and normalized["epoch"] < previous:
                raise ValueError(f"segment is not append ordered: {path}")
            previous = normalized["epoch"]

    def _validate_tail(self, tick: dict) -> None:
        if self._last_epoch is None:
            return
        epoch = tick["epoch"]
        fingerprint = self._fingerprint(tick)
        if epoch < self._last_epoch:
            raise ValueError("segments are not globally causal")
        if epoch == self._last_epoch and fingerprint in self._tail_fingerprints:
            raise ValueError("segments duplicate their causal tail")

    def _advance_tail(self, tick: dict) -> None:
        self._validate_tail(tick)
        epoch = tick["epoch"]
        fingerprint = self._fingerprint(tick)
        if self._last_epoch != epoch:
            self._last_epoch = epoch
            self._tail_fingerprints = {fingerprint}
        else:
            self._tail_fingerprints.add(fingerprint)

    def _open_segment(self, day: tuple[int, int, int]) -> None:
        year, month, date = day
        directory = self.root_path / self.symbol / f"{year:04d}" / f"{month:02d}" / f"{date:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        self._partial_path = directory / f"{self._next_sequence:020d}-{uuid.uuid4().hex}.jsonl.gz.partial"
        self._day = day
        self._ticks = []
        self._fingerprints = set()

    def _clear_active_segment(self) -> None:
        self._partial_path = None
        self._day = None
        self._ticks = []
        self._fingerprints = set()

    @classmethod
    def _read_ticks(cls, path: Path, recover_partial: bool = False) -> list[dict]:
        data = path.read_bytes()
        offset = 0
        ticks: list[dict] = []
        while offset < len(data):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                payload = decoder.decompress(data[offset:]) + decoder.flush()
            except zlib.error:
                payload = b""
            if not decoder.eof:
                if recover_partial:
                    cls._quarantine_partial_tail(path, data[offset:], offset)
                    break
                raise ValueError(f"gzip segment is truncated or corrupt: {path}")
            consumed = len(data[offset:]) - len(decoder.unused_data)
            if consumed <= 0 or not payload.endswith(b"\n"):
                if recover_partial:
                    cls._quarantine_partial_tail(path, data[offset:], offset)
                    break
                raise ValueError(f"gzip member is incomplete: {path}")
            try:
                member_ticks = [cls._normalize_tick(json.loads(line)) for line in payload.decode("utf-8").splitlines() if line]
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                if recover_partial:
                    cls._quarantine_partial_tail(path, data[offset:], offset)
                    break
                raise ValueError(f"gzip member has invalid JSONL: {path}") from exc
            ticks.extend(member_ticks)
            offset += consumed
        return ticks

    @staticmethod
    def _quarantine_partial_tail(path: Path, tail: bytes, valid_end: int) -> None:
        if tail:
            quarantine = path.with_name(f"{path.name}.{uuid.uuid4().hex}.corrupt")
            with quarantine.open("xb") as stream:
                stream.write(tail)
                stream.flush()
                os.fsync(stream.fileno())
        with path.open("r+b") as stream:
            stream.truncate(valid_end)
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def _append_member(cls, path: Path, tick: dict) -> None:
        payload = (json.dumps(tick, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        compressor = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
        member = compressor.compress(payload) + compressor.flush()
        with path.open("ab") as stream:
            stream.write(member)
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def _normalize_tick(cls, tick: Mapping[str, object]) -> dict:
        if not isinstance(tick, Mapping) or "epoch" not in tick or "quote" not in tick:
            raise ValueError("tick must contain epoch and quote")
        if "symbol" in tick and tick["symbol"] != NEXUS_SYMBOL:
            raise ValueError(f"TickArchive only accepts {NEXUS_SYMBOL} ticks")
        normalized = dict(tick)
        normalized["epoch"] = cls._require_epoch(tick["epoch"], "epoch")
        quote = tick["quote"]
        if isinstance(quote, bool) or not isinstance(quote, (int, float)) or not math.isfinite(float(quote)):
            raise ValueError("quote must be finite")
        normalized["quote"] = float(quote)
        return normalized

    @staticmethod
    def _require_epoch(value: object, name: str) -> int:
        if isinstance(value, bool) or type(value) is not int:
            raise ValueError(f"{name} must be an integer epoch")
        return value

    @staticmethod
    def _fingerprint(tick: Mapping[str, object]) -> str:
        return json.dumps(dict(tick), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _utc_day(epoch: int) -> tuple[int, int, int]:
        try:
            date = datetime.fromtimestamp(epoch, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("epoch is outside the supported UTC range") from exc
        return date.year, date.month, date.day

    @staticmethod
    def _sequence_from_path(path: Path) -> int | None:
        prefix = path.name.split("-", 1)[0]
        return int(prefix) if len(prefix) == 20 and prefix.isdigit() and int(prefix) > 0 else None
