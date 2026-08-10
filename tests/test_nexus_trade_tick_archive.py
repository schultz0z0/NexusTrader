import hashlib
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from nexus_trade.tick_archive import TickArchive


class TickArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "ticks"
        self.db_path = Path(self.tempdir.name) / "manifest.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def _child(self, program: str) -> list[str]:
        return [sys.executable, "-c", textwrap.dedent(program), str(self.root), str(self.db_path)]

    def _start_lock_holder(self, *, context_manager: bool) -> subprocess.Popen:
        constructor = (
            "with TickArchive(root, db_path):\n"
            "    print('LOCKED', flush=True)\n"
            "    sys.stdin.readline()"
            if context_manager else
            "archive = TickArchive(root, db_path)\n"
            "print('LOCKED', flush=True)\n"
            "sys.stdin.readline()\n"
            "archive.close()"
        )
        program = (
            "import sys\n"
            "from pathlib import Path\n"
            "from nexus_trade.tick_archive import TickArchive\n"
            "root, db_path = Path(sys.argv[1]), Path(sys.argv[2])\n"
            f"{constructor}\n"
        )
        process = subprocess.Popen(
            self._child(program),
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(process.stdout.readline().strip(), "LOCKED")
        return process

    def _assert_child_can_acquire_writer(self):
        result = subprocess.run(
            self._child("""
                import sys
                from pathlib import Path
                from nexus_trade.tick_archive import TickArchive
                archive = TickArchive(Path(sys.argv[1]), Path(sys.argv[2]))
                archive.close()
            """),
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_closing_daily_segments_records_hash_manifest_and_replays_ticks_in_append_order(self):
        archive = TickArchive(self.root, self.db_path)
        ticks = [
            {"epoch": 86_399, "quote": 1.0},
            {"epoch": 86_400, "quote": 1.1},
            {"epoch": 86_401, "quote": 1.2},
        ]

        for tick in ticks:
            archive.append(tick)
        manifest = archive.close_segment()

        self.assertEqual(list(archive.replay(86_399, 86_401)), ticks)
        self.assertTrue(manifest["sha256"])
        self.assertEqual(
            manifest["sha256"],
            hashlib.sha256(Path(manifest["path"]).read_bytes()).hexdigest(),
        )
        self.assertEqual(manifest["tick_count"], 2)
        self.assertEqual(manifest["start_epoch"], 86_400)
        self.assertEqual(manifest["end_epoch"], 86_401)
        self.assertGreater(manifest["byte_count"], 0)
        self.assertTrue((self.root / "R_100" / "1970" / "01" / "01").exists())
        self.assertTrue((self.root / "R_100" / "1970" / "01" / "02").exists())

        db = sqlite3.connect(self.db_path)
        try:
            rows = db.execute(
                "SELECT symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path, segment_sequence "
                "FROM nexus_tick_segments ORDER BY start_epoch"
            ).fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][:4], ("R_100", 86_400, 86_401, 2))
        self.assertEqual(rows[-1][4:7], (manifest["byte_count"], manifest["sha256"], manifest["path"]))
        self.assertEqual([row[-1] for row in rows], [1, 2])

    def test_restart_recovers_partial_segment_without_reordering_or_duplicating_ticks(self):
        first = TickArchive(self.root, self.db_path)
        first.append({"epoch": 100, "quote": 1.0})
        first.close()

        recovered = TickArchive(self.root, self.db_path)
        recovered.append({"epoch": 100, "quote": 1.0})
        recovered.append({"epoch": 101, "quote": 1.1})
        recovered.close_segment()

        self.assertEqual(
            list(recovered.replay(100, 101)),
            [{"epoch": 100, "quote": 1.0}, {"epoch": 101, "quote": 1.1}],
        )

    def test_restart_restores_a_manifest_for_a_segment_published_before_a_crash(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 100, "quote": 1.0})
        archive.close_segment()
        archive.close()
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("DELETE FROM nexus_tick_segments")
            db.commit()
        finally:
            db.close()

        recovered = TickArchive(self.root, self.db_path)

        db = sqlite3.connect(self.db_path)
        try:
            count = db.execute("SELECT COUNT(*) FROM nexus_tick_segments").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(count, 1)
        recovered.close()

    def test_append_rejects_a_tick_that_would_break_causal_order(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 101, "quote": 1.1})

        with self.assertRaises(ValueError):
            archive.append({"epoch": 100, "quote": 1.0})

    def test_replay_uses_persisted_sequence_when_same_day_names_sort_in_reverse_order(self):
        archive = TickArchive(self.root, self.db_path)
        first_tick = {"epoch": 100, "quote": 1.0}
        second_tick = {"epoch": 101, "quote": 1.1}
        archive.append(first_tick)
        first = archive.close_segment()
        archive.append(second_tick)
        second = archive.close_segment()
        archive.close()

        first_path = Path(first["path"])
        second_path = Path(second["path"])
        renamed_first = first_path.with_name("z-first.jsonl.gz")
        renamed_second = second_path.with_name("a-second.jsonl.gz")
        os.replace(first_path, renamed_first)
        os.replace(second_path, renamed_second)
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE nexus_tick_segments SET path = ? WHERE sha256 = ?", (str(renamed_first), first["sha256"]))
            db.execute("UPDATE nexus_tick_segments SET path = ? WHERE sha256 = ?", (str(renamed_second), second["sha256"]))
            db.commit()
        finally:
            db.close()

        recovered = TickArchive(self.root, self.db_path)

        self.assertEqual(list(recovered.replay(100, 101)), [first_tick, second_tick])

    def test_restart_rebuilds_global_tail_for_retry_deduplication_and_backdating_rejection(self):
        archived = TickArchive(self.root, self.db_path)
        tick = {"epoch": 100, "quote": 1.0}
        archived.append(tick)
        archived.close_segment()
        archived.close()

        recovered = TickArchive(self.root, self.db_path)
        recovered.append(tick)
        with self.assertRaises(ValueError):
            recovered.append({"epoch": 99, "quote": 0.9})
        recovered.append({"epoch": 101, "quote": 1.1})
        recovered.close_segment()

        self.assertEqual(list(recovered.replay(0, 200)), [tick, {"epoch": 101, "quote": 1.1}])

    def test_restart_keeps_complete_gzip_members_when_a_partial_tail_is_truncated(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 100, "quote": 1.0})
        with Path(archive._partial_path).open("ab") as stream:
            stream.write(b"\x1f\x8b\x08\x00")
        archive.close()

        recovered = TickArchive(self.root, self.db_path)
        recovered.append({"epoch": 101, "quote": 1.1})
        recovered.close_segment()

        self.assertEqual(
            list(recovered.replay(100, 101)),
            [{"epoch": 100, "quote": 1.0}, {"epoch": 101, "quote": 1.1}],
        )

    def test_reconciliation_fails_closed_for_a_tampered_manifest(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 100, "quote": 1.0})
        manifest = archive.close_segment()
        archive.close()
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE nexus_tick_segments SET byte_count = 0 WHERE sha256 = ?", (manifest["sha256"],))
            db.commit()
        finally:
            db.close()

        with self.assertRaises(ValueError):
            TickArchive(self.root, self.db_path)

    def test_append_rejects_invalid_epochs_quotes_and_other_symbols(self):
        archive = TickArchive(self.root, self.db_path)
        invalid_ticks = [
            {"epoch": True, "quote": 1.0},
            {"epoch": 1.5, "quote": 1.0},
            {"epoch": "100", "quote": 1.0},
            {"epoch": 100, "quote": math.inf},
            {"epoch": 100, "quote": math.nan},
            {"epoch": 100, "quote": 1.0, "symbol": "R_75"},
        ]

        for tick in invalid_ticks:
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                archive.append(tick)

    def test_replay_bounds_are_inclusive_and_exclude_outside_ticks(self):
        archive = TickArchive(self.root, self.db_path)
        for tick in ({"epoch": 100, "quote": 1.0}, {"epoch": 101, "quote": 1.1}, {"epoch": 102, "quote": 1.2}):
            archive.append(tick)
        archive.close_segment()

        self.assertEqual(list(archive.replay(101, 101)), [{"epoch": 101, "quote": 1.1}])

    def test_second_writer_fails_until_the_first_writer_explicitly_releases_its_lock(self):
        first = TickArchive(self.root, self.db_path)

        with self.assertRaises(RuntimeError):
            TickArchive(self.root, self.db_path)

        first.close()
        second = TickArchive(self.root, self.db_path)
        second.close()

    def test_legacy_uuid_orphan_after_manifest_tail_gets_the_next_causal_sequence(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 100, "quote": 1.0})
        archive.close_segment()
        archive.close()
        orphan = self.root / "R_100" / "1970" / "01" / "01" / "legacy-uuid.jsonl.gz"
        TickArchive._append_member(orphan, {"epoch": 101, "quote": 1.1})

        recovered = TickArchive(self.root, self.db_path)

        self.assertEqual(
            list(recovered.replay(100, 101)),
            [{"epoch": 100, "quote": 1.0}, {"epoch": 101, "quote": 1.1}],
        )
        recovered.close()

    def test_multiple_legacy_uuid_orphans_fail_instead_of_being_arbitrarily_ordered(self):
        directory = self.root / "R_100" / "1970" / "01" / "01"
        directory.mkdir(parents=True)
        TickArchive._append_member(directory / "legacy-a.jsonl.gz", {"epoch": 100, "quote": 1.0})
        TickArchive._append_member(directory / "legacy-b.jsonl.gz", {"epoch": 101, "quote": 1.1})

        with self.assertRaises(ValueError):
            TickArchive(self.root, self.db_path)

    def test_os_lock_rejects_a_real_second_process_without_creating_tick_artifacts(self):
        holder = self._start_lock_holder(context_manager=False)
        try:
            contender = subprocess.run(
                self._child("""
                    import sys
                    from pathlib import Path
                    from nexus_trade.tick_archive import TickArchive
                    try:
                        TickArchive(Path(sys.argv[1]), Path(sys.argv[2]))
                    except RuntimeError:
                        sys.exit(0)
                    sys.exit(1)
                """),
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(contender.returncode, 0, contender.stderr)
            self.assertEqual(list(self.root.rglob("*.partial")), [])
            self.assertEqual(list(self.root.rglob("*.jsonl.gz")), [])
            db = sqlite3.connect(self.db_path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM nexus_tick_segments").fetchone()[0], 0)
            finally:
                db.close()
        finally:
            stdout, stderr = holder.communicate("\n", timeout=5)
            self.assertEqual(holder.returncode, 0, f"{stdout}\n{stderr}")

    def test_context_manager_releases_the_os_lock_for_a_later_process(self):
        holder = self._start_lock_holder(context_manager=True)
        stdout, stderr = holder.communicate("\n", timeout=5)
        self.assertEqual(holder.returncode, 0, f"{stdout}\n{stderr}")

        self._assert_child_can_acquire_writer()

    def test_initialization_error_releases_the_os_lock_for_a_later_process(self):
        directory = self.root / "R_100" / "1970" / "01" / "01"
        directory.mkdir(parents=True)
        first = directory / "legacy-a.jsonl.gz"
        second = directory / "legacy-b.jsonl.gz"
        TickArchive._append_member(first, {"epoch": 100, "quote": 1.0})
        TickArchive._append_member(second, {"epoch": 101, "quote": 1.1})
        result = subprocess.run(
            self._child("""
                import sys
                from pathlib import Path
                from nexus_trade.tick_archive import TickArchive
                try:
                    TickArchive(Path(sys.argv[1]), Path(sys.argv[2]))
                except ValueError:
                    sys.exit(0)
                sys.exit(1)
            """),
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob("*.partial")), [])
        first.unlink()
        second.unlink()

        self._assert_child_can_acquire_writer()


if __name__ == "__main__":
    unittest.main()
