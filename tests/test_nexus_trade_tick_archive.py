import sqlite3
import tempfile
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
        self.assertEqual(manifest["tick_count"], 2)
        self.assertEqual(manifest["start_epoch"], 86_400)
        self.assertEqual(manifest["end_epoch"], 86_401)
        self.assertGreater(manifest["byte_count"], 0)
        self.assertTrue((self.root / "R_100" / "1970" / "01" / "01").exists())
        self.assertTrue((self.root / "R_100" / "1970" / "01" / "02").exists())

        db = sqlite3.connect(self.db_path)
        try:
            rows = db.execute(
                "SELECT symbol, start_epoch, end_epoch, tick_count, byte_count, sha256, path "
                "FROM nexus_tick_segments ORDER BY start_epoch"
            ).fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][:4], ("R_100", 86_400, 86_401, 2))
        self.assertEqual(rows[-1][4:], (manifest["byte_count"], manifest["sha256"], manifest["path"]))

    def test_restart_recovers_partial_segment_without_reordering_or_duplicating_ticks(self):
        first = TickArchive(self.root, self.db_path)
        first.append({"epoch": 100, "quote": 1.0})

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
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("DELETE FROM nexus_tick_segments")
            db.commit()
        finally:
            db.close()

        TickArchive(self.root, self.db_path)

        db = sqlite3.connect(self.db_path)
        try:
            count = db.execute("SELECT COUNT(*) FROM nexus_tick_segments").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(count, 1)

    def test_append_rejects_a_tick_that_would_break_causal_order(self):
        archive = TickArchive(self.root, self.db_path)
        archive.append({"epoch": 101, "quote": 1.1})

        with self.assertRaises(ValueError):
            archive.append({"epoch": 100, "quote": 1.0})


if __name__ == "__main__":
    unittest.main()
