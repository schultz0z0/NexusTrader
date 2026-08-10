import concurrent.futures
import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError

from nexus_trade.artifacts import CandidateArtifact, ArtifactIntegrityError
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.dataset import DatasetBuilder, DatasetRejectedError
from nexus_trade.training import (
    SQLiteTrialLedger,
    Trainer,
    TrainingConfig,
    TrainingRejectedError,
)


PROVENANCE = "a" * 64


def settled_rows(count=30, *, labels=None, horizon=90):
    labels = labels or [index % 2 for index in range(count)]
    rows = []
    for index in range(count):
        feature_epoch = 60_000 + index * 180
        rows.append(
            {
                "contract_id": 10_000 + index,
                "symbol": "R_100",
                "timeframe_seconds": 60,
                "feature_epoch": feature_epoch,
                "entry_epoch": feature_epoch + 60,
                "label_epoch": feature_epoch + horizon,
                "settled": True,
                "status": "closed",
                "contract_type": "CALL" if index % 2 else "PUT",
                "provenance_hash": PROVENANCE,
                "features": {
                    "adx": float(10 + index % 11),
                    "bollinger_percent_b": float(index % 7) / 6.0,
                    "bollinger_width": 0.5 + float(index % 5) / 10.0,
                },
                "label": labels[index],
                "stake": 1.0,
                "payout": 1.8,
            }
        )
    return rows


class DatasetBuilderTests(unittest.TestCase):
    def build(self, rows=None, *, cutoff=100_000, minimum_rows=12):
        return DatasetBuilder(
            settled_rows() if rows is None else rows,
            expected_provenance_hash=PROVENANCE,
            minimum_rows=minimum_rows,
            purge_seconds=0,
        ).build(cutoff)

    def test_build_is_immutable_chronological_and_purges_label_horizons(self):
        dataset = self.build(settled_rows(horizon=240))
        all_rows = dataset.train.rows + dataset.validation.rows + dataset.test.rows
        self.assertEqual(
            [row.feature_epoch for row in all_rows],
            sorted(row.feature_epoch for row in all_rows),
        )
        self.assertLess(
            max(row.label_epoch for row in dataset.train.rows),
            min(row.feature_epoch for row in dataset.validation.rows),
        )
        self.assertLess(
            max(row.label_epoch for row in dataset.validation.rows),
            min(row.feature_epoch for row in dataset.test.rows),
        )
        with self.assertRaises(FrozenInstanceError):
            dataset.cutoff_epoch = 123
        with self.assertRaises(TypeError):
            dataset.train.rows[0].features["adx"] = 999

    def test_cutoff_is_exclusive_and_future_labels_are_rejected(self):
        rows = settled_rows(12)
        rows[-1]["label_epoch"] = 70_000
        with self.assertRaisesRegex(DatasetRejectedError, "cutoff"):
            self.build(rows, cutoff=70_000)

    def test_rejects_duplicate_unordered_incomplete_and_wrong_provenance_rows(self):
        mutations = []
        duplicate = settled_rows(12)
        duplicate[5]["contract_id"] = duplicate[4]["contract_id"]
        mutations.append(duplicate)
        unordered = settled_rows(12)
        unordered[5], unordered[6] = unordered[6], unordered[5]
        mutations.append(unordered)
        incomplete = settled_rows(12)
        del incomplete[5]["payout"]
        mutations.append(incomplete)
        provenance = settled_rows(12)
        provenance[5]["provenance_hash"] = "b" * 64
        mutations.append(provenance)
        for rows in mutations:
            with self.subTest(rows=rows):
                with self.assertRaises(DatasetRejectedError):
                    self.build(rows)

    def test_rejects_nonsettled_noncausal_cross_symbol_non_m1_and_direction_feature(self):
        updates = (
            {"settled": False},
            {"feature_epoch": 61_000, "entry_epoch": 61_000},
            {"symbol": "R_75"},
            {"timeframe_seconds": 300},
            {"status": "open"},
        )
        for update in updates:
            rows = settled_rows(12)
            rows[5].update(update)
            with self.subTest(update=update):
                with self.assertRaises(DatasetRejectedError):
                    self.build(rows)
        rows = settled_rows(12)
        rows[5]["label_epoch"] = rows[5]["entry_epoch"]
        with self.assertRaisesRegex(DatasetRejectedError, "labels"):
            self.build(rows)
        rows = settled_rows(12)
        rows[5]["features"]["contract_type"] = 1.0
        with self.assertRaisesRegex(DatasetRejectedError, "direction"):
            self.build(rows)

    def test_minimum_data_fails_closed(self):
        with self.assertRaisesRegex(DatasetRejectedError, "minimum"):
            self.build(settled_rows(11), minimum_rows=12)


class TrainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "learning.db")
        self.ledger = SQLiteTrialLedger(self.db_path)
        self.dataset = DatasetBuilder(
            settled_rows(36),
            expected_provenance_hash=PROVENANCE,
            minimum_rows=12,
            purge_seconds=0,
        ).build(100_000)

    def tearDown(self):
        self.temp.cleanup()

    def trainer(self):
        return Trainer(
            TrainingConfig(
                seed=73,
                stake=1.0,
                payout=1.8,
                safety_margin=0.04,
                margin_version="break-even-v1",
                minimum_train_rows=8,
                max_iter=25,
            )
        )

    def test_training_is_reproducible_and_gate_never_learns_direction(self):
        first = self.trainer().fit(self.dataset, self.ledger)
        second = self.trainer().fit(self.dataset, self.ledger)
        self.assertEqual(first.artifact_hash, second.artifact_hash)
        self.assertEqual(first.metadata_hash, second.metadata_hash)
        self.assertEqual(first.metadata["feature_schema"], second.metadata["feature_schema"])
        self.assertEqual(first.metadata["metrics"], second.metadata["metrics"])
        self.assertNotIn("contract_type", first.metadata["feature_schema"])
        self.assertEqual(first.metadata["direction_source"], "bollinger_v1_deterministic")
        self.assertEqual(first.metadata["gate_actions"], ("DO_NOT_OPERATE", "OPERATE"))
        self.assertNotIn("direction", first.metadata["model"]["inputs"])

    def test_threshold_is_break_even_plus_versioned_margin(self):
        artifact = self.trainer().fit(self.dataset, self.ledger)
        threshold = artifact.metadata["operate_threshold"]
        self.assertAlmostEqual(threshold["value"], 1.0 / 1.8 + 0.04, places=12)
        self.assertEqual(threshold["margin_version"], "break-even-v1")

    def test_one_class_and_tiny_training_fail_closed_and_are_ledgered(self):
        one_class = DatasetBuilder(
            settled_rows(30, labels=[1] * 30),
            expected_provenance_hash=PROVENANCE,
            minimum_rows=12,
            purge_seconds=0,
        ).build(100_000)
        with self.assertRaisesRegex(TrainingRejectedError, "one class"):
            self.trainer().fit(one_class, self.ledger)
        tiny_trainer = Trainer(
            TrainingConfig(seed=73, minimum_train_rows=100, max_iter=5)
        )
        with self.assertRaisesRegex(TrainingRejectedError, "tiny"):
            tiny_trainer.fit(self.dataset, self.ledger)
        attempts = self.ledger.list_attempts()
        self.assertEqual([item["status"] for item in attempts], ["REJECTED", "REJECTED"])
        self.assertTrue(all(item["result_action"] == "DO_NOT_OPERATE" for item in attempts))

    def test_success_ledger_contains_reproducibility_metrics_and_ablations(self):
        artifact = self.trainer().fit(self.dataset, self.ledger)
        attempts = self.ledger.list_attempts()
        self.assertEqual(len(attempts), 1)
        attempt = attempts[0]
        self.assertEqual(attempt["dataset_hash"], self.dataset.dataset_hash)
        self.assertEqual(attempt["provenance_hash"], PROVENANCE)
        self.assertEqual(attempt["seed"], 73)
        self.assertEqual(attempt["status"], "SUCCEEDED")
        self.assertEqual(attempt["artifact_hash"], artifact.artifact_hash)
        self.assertEqual(attempt["feature_schema"], list(self.dataset.feature_schema))
        self.assertEqual(attempt["trial_count"], 1 + len(self.dataset.feature_schema))
        self.assertEqual(set(attempt["metrics"]), {"train", "validation", "test"})
        self.assertEqual(len(attempt["ablations"]), len(self.dataset.feature_schema))


class ArtifactAndRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "registry.db")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def artifact(name="candidate-a"):
        return CandidateArtifact.create(
            {
                "schema_version": 1,
                "candidate_name": name,
                "dataset_hash": "c" * 64,
                "provenance_hash": PROVENANCE,
                "seed": 73,
                "model": {"family": "HistGradientBoostingClassifier"},
                "feature_schema": ["adx"],
                "metrics": {"test": {"accuracy": 0.5}},
                "ablations": [],
                "trial_count": 1,
                "direction_source": "bollinger_v1_deterministic",
                "gate_actions": ["DO_NOT_OPERATE", "OPERATE"],
            }
        )

    def test_json_artifact_is_content_addressed_immutable_and_rejects_tampering(self):
        artifact = self.artifact()
        restored = CandidateArtifact.from_json(artifact.to_json())
        self.assertEqual(restored.artifact_hash, artifact.artifact_hash)
        with self.assertRaises(TypeError):
            restored.metadata["seed"] = 1
        envelope = json.loads(artifact.to_json())
        envelope["metadata"]["seed"] = 99
        with self.assertRaises(ArtifactIntegrityError):
            CandidateArtifact.from_json(json.dumps(envelope))

    def test_artifact_rejects_secrets_absolute_paths_and_pickle_payloads(self):
        for field, value in (
            ("api_token", "secret"),
            ("api_key", "secret"),
            ("source_path", os.path.abspath("private.db")),
            ("pickle", "gASVunsafe"),
        ):
            metadata = dict(self.artifact().metadata)
            metadata[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    CandidateArtifact.create(metadata)

    def test_registry_is_concurrent_idempotent_and_selects_exactly_one_trial(self):
        artifacts = [self.artifact(f"candidate-{index}") for index in range(12)]

        def register(artifact):
            return CandidateRegistry(self.db_path).register(artifact)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(register, artifacts + [artifacts[0]] * 4))
        self.assertTrue(all(item["status"] in {"TRIAL", "SHADOW"} for item in results))
        listed = CandidateRegistry(self.db_path).list_candidates()
        self.assertEqual(len(listed), len(artifacts))
        self.assertEqual(sum(item["status"] == "TRIAL" for item in listed), 1)
        self.assertEqual(sum(item["status"] == "SHADOW" for item in listed), 11)

    def test_registry_verifies_hash_and_database_rows_are_append_only(self):
        artifact = self.artifact()
        registry = CandidateRegistry(self.db_path)
        row = registry.register(artifact)
        object.__setattr__(artifact, "artifact_hash", "0" * 64)
        with self.assertRaises(ArtifactIntegrityError):
            registry.register(artifact)
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE nexus_candidates SET metadata = '{}' WHERE id = ?",
                    (row["id"],),
                )


if __name__ == "__main__":
    unittest.main()
