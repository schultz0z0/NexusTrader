import concurrent.futures
import contextlib
import hashlib
import inspect
import json
import math
import os
import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from database.models import DatabaseModels
from nexus_trade.artifacts import (
    ArtifactIntegrityError,
    CandidateArtifact,
    canonical_json,
)
from nexus_trade.candidates import CandidateRegistry
from nexus_trade.dataset import DatasetBuilder, DatasetRejectedError
from nexus_trade.indicators import IndicatorFrame
from nexus_trade.strategy import NexusTradeStrategy
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
        label = labels[index]
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
                "label": label,
                "stake": 1.0,
                "payout": 1.8 if label == 1 else 0.0,
                "profit": 0.8 if label == 1 else -1.0,
                "result": "won" if label == 1 else "lost",
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

    def test_accepts_real_mixed_settlements_and_validates_outcome_semantics(self):
        rows = settled_rows(15)
        rows[4].update(result="tie", label=0, payout=1.0, profit=0.0)
        try:
            dataset = self.build(rows, minimum_rows=12)
        except DatasetRejectedError as exc:
            self.fail(f"real settled losses and ties must be accepted: {exc}")
        self.assertEqual({row.label for row in dataset.rows}, {0, 1})

        invalid = (
            {"payout": -0.01},
            {"profit": float("nan")},
            {"result": "lost", "label": 1, "payout": 0.0, "profit": -1.0},
            {"result": "won", "label": 1, "payout": 0.0, "profit": -1.0},
            {"result": "tie", "label": 0, "payout": 0.0, "profit": 0.0},
        )
        for update in invalid:
            malformed = settled_rows(15)
            malformed[4].update(update)
            with self.subTest(update=update):
                with self.assertRaises(DatasetRejectedError):
                    self.build(malformed, minimum_rows=12)


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
                offered_payout_multiplier=1.8,
                payout_assumption_version="deriv-offer-gross-v1",
                safety_margin=0.04,
                margin_version="break-even-v1",
                minimum_train_rows=8,
                max_iter=25,
            )
        )

    @staticmethod
    def with_threshold(artifact, *, multiplier, margin):
        metadata = json.loads(artifact.to_json())["metadata"]
        metadata["training_config"]["offered_payout_multiplier"] = multiplier
        metadata["training_config"]["safety_margin"] = margin
        metadata["configuration_hash"] = hashlib.sha256(
            canonical_json(metadata["training_config"]).encode("utf-8")
        ).hexdigest()
        threshold = metadata["operate_threshold"]
        threshold["value"] = 1.0 / multiplier + margin
        threshold["break_even_probability"] = 1.0 / multiplier
        threshold["offered_payout_multiplier"] = multiplier
        threshold["safety_margin"] = margin
        return CandidateArtifact.create(metadata)

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
        self.assertEqual(
            threshold["payout_assumption_version"], "deriv-offer-gross-v1",
        )
        self.assertEqual(threshold["offered_payout_multiplier"], 1.8)

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

    def test_fitted_artifact_round_trip_matches_sklearn_for_numeric_and_missing_values(self):
        artifact = self.trainer().fit(self.dataset, self.ledger)
        self.assertEqual(artifact.metadata["schema_version"], 2)
        self.assertEqual(artifact.metadata["model"]["serialization"], "hgb_tree_json_v1")
        self.assertIn("fitted_model", artifact.metadata)

        restored = CandidateArtifact.from_json(artifact.to_json())
        executable_gate = getattr(restored, "executable_gate", None)
        self.assertTrue(callable(executable_gate), "artifact must expose its executable gate")
        gate = executable_gate()

        train_x = np.asarray(
            [
                [row.features[name] for name in self.dataset.feature_schema]
                for row in self.dataset.train.rows
            ],
            dtype=np.float64,
        )
        train_y = np.asarray(
            [row.label for row in self.dataset.train.rows], dtype=np.int64,
        )
        reference = HistGradientBoostingClassifier(
            random_state=73,
            early_stopping=False,
            max_iter=25,
            learning_rate=0.1,
            max_leaf_nodes=15,
        ).fit(train_x, train_y)
        samples = np.asarray(
            [
                [20.0, 0.5, 0.7],
                [np.nan, 0.25, 0.8],
                [15.0, np.nan, np.nan],
            ],
            dtype=np.float64,
        )
        expected = reference.predict_proba(samples)[:, 1]
        observed = np.asarray(
            [gate.predict_probability(row) for row in samples], dtype=np.float64,
        )
        np.testing.assert_allclose(observed, expected, rtol=0.0, atol=1e-15)

    def test_executable_artifact_rejects_invalid_threshold_and_tampered_fitted_state(self):
        artifact = self.trainer().fit(self.dataset, self.ledger)
        self.assertIn("fitted_model", artifact.metadata)
        envelope = json.loads(artifact.to_json())

        for invalid_threshold in (0.0, -0.01, 1.01, float("inf")):
            with self.subTest(threshold=invalid_threshold):
                invalid = json.loads(json.dumps(envelope))
                invalid["metadata"]["operate_threshold"]["value"] = invalid_threshold
                with self.assertRaises((ValueError, ArtifactIntegrityError)):
                    CandidateArtifact.create(invalid["metadata"])

        tampered = json.loads(json.dumps(envelope))
        tampered["metadata"]["fitted_model"]["trees"][0][0]["left"] = 999999
        with self.assertRaises((ValueError, ArtifactIntegrityError)):
            CandidateArtifact.create(tampered["metadata"])

    def test_ml_gate_only_blocks_a_deterministic_direction_and_permissive_gate_preserves_it(self):
        self.assertIn("gate", inspect.signature(NexusTradeStrategy).parameters)
        artifact = self.trainer().fit(self.dataset, self.ledger)
        high_gate = self.with_threshold(
            artifact, multiplier=2.0, margin=0.5,
        ).executable_gate()
        permissive_gate = self.with_threshold(
            artifact, multiplier=1000.0, margin=0.0,
        ).executable_gate()
        candle = {
            "time": 0, "open": 99.0, "high": 101.0, "low": 99.0,
            "close": 101.0, "is_closed": True, "close_epoch": 60,
        }
        indicators = IndicatorFrame(
            epoch=0, upper=110.0, middle=100.0, lower=90.0, adx=20.0,
            values={"bollinger_percent_b": 0.5, "bollinger_width": 0.7},
        )

        blocked = NexusTradeStrategy(gate=high_gate).on_closed_candle(
            candle, indicators,
        )[0]
        allowed = NexusTradeStrategy(gate=permissive_gate).on_closed_candle(
            candle, indicators,
        )[0]

        self.assertEqual((blocked.contract_type, blocked.blocked_reason), ("CALL", "ML_BLOCKED"))
        self.assertEqual(blocked.reason_codes[-1], "ml_probability_below_threshold")
        self.assertEqual((allowed.contract_type, allowed.blocked_reason), ("CALL", None))

    def test_ml_gate_cannot_create_no_trade_bypass_adx_or_operate_on_invalid_features(self):
        self.assertIn("gate", inspect.signature(NexusTradeStrategy).parameters)
        artifact = self.trainer().fit(self.dataset, self.ledger)
        gate = self.with_threshold(
            artifact, multiplier=1000.0, margin=0.0,
        ).executable_gate()
        no_trade = NexusTradeStrategy(gate=gate).on_closed_candle(
            {
                "time": 0, "open": 99.0, "high": 100.0, "low": 99.0,
                "close": 100.0, "is_closed": True, "close_epoch": 60,
            },
            IndicatorFrame(
                epoch=0, upper=110.0, middle=100.0, lower=90.0, adx=20.0,
                values={"bollinger_percent_b": 0.5, "bollinger_width": 0.7},
            ),
        )[0]
        adx_blocked = NexusTradeStrategy(gate=gate).on_closed_candle(
            {
                "time": 0, "open": 99.0, "high": 101.0, "low": 99.0,
                "close": 101.0, "is_closed": True, "close_epoch": 60,
            },
            IndicatorFrame(
                epoch=0, upper=110.0, middle=100.0, lower=90.0, adx=23.0,
                values={"bollinger_percent_b": 0.5, "bollinger_width": 0.7},
            ),
        )[0]
        missing_feature = NexusTradeStrategy(gate=gate).on_closed_candle(
            {
                "time": 0, "open": 99.0, "high": 101.0, "low": 99.0,
                "close": 101.0, "is_closed": True, "close_epoch": 60,
            },
            IndicatorFrame(
                epoch=0, upper=110.0, middle=100.0, lower=90.0, adx=20.0,
                values={"bollinger_percent_b": 0.5},
            ),
        )[0]

        self.assertEqual((no_trade.contract_type, no_trade.blocked_reason), (None, "NO_TRADE"))
        self.assertEqual((adx_blocked.contract_type, adx_blocked.blocked_reason), ("CALL", "ADX_BLOCKED"))
        self.assertEqual((missing_feature.contract_type, missing_feature.blocked_reason), ("CALL", "ML_BLOCKED"))
        self.assertEqual(missing_feature.reason_codes[-1], "ml_gate_input_invalid")


class ArtifactAndRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "registry.db")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def artifact(name="candidate-a"):
        training_config = {
            "seed": 73,
            "offered_payout_multiplier": 1.8,
            "payout_assumption_version": "deriv-offer-gross-v1",
            "safety_margin": 0.04,
            "margin_version": "break-even-v1",
            "minimum_train_rows": 8,
            "max_iter": 25,
            "learning_rate": 0.1,
            "max_leaf_nodes": 15,
        }
        import hashlib
        from nexus_trade.artifacts import canonical_json
        configuration_hash = hashlib.sha256(
            canonical_json(training_config).encode("utf-8")
        ).hexdigest()
        return CandidateArtifact.create(
            {
                "schema_version": 2,
                "artifact_type": "nexus_trade_shadow_candidate",
                "candidate_name": name,
                "contract": {
                    "symbol": "R_100",
                    "timeframe_seconds": 60,
                    "duration_seconds": 58,
                },
                "dataset_hash": "c" * 64,
                "provenance_hash": PROVENANCE,
                "configuration_hash": configuration_hash,
                "training_config": training_config,
                "seed": 73,
                "indicator_configuration": {
                    "bollinger": {"period": 20, "std_dev": 2.0, "ma": "SMA"},
                    "adx": {"period": 14},
                    "direction_contract": "bollinger_v1_deterministic",
                },
                "model": {
                    "family": "HistGradientBoostingClassifier",
                    "inputs": ["adx"],
                    "output": "win_probability",
                    "serialization": "hgb_tree_json_v1",
                },
                "fitted_model": {
                    "schema_version": 1,
                    "family": "HistGradientBoostingClassifier",
                    "link": "logit",
                    "n_features": 1,
                    "classes": [0, 1],
                    "baseline": math.log(4.0),
                    "trees": [
                        [{
                            "value": 0.0,
                            "feature_index": 0,
                            "threshold": 0.0,
                            "missing_go_to_left": False,
                            "left": 0,
                            "right": 0,
                            "is_leaf": True,
                        }]
                        for _ in range(25)
                    ],
                },
                "feature_schema": ["adx"],
                "operate_threshold": {
                    "value": 1.0 / 1.8 + 0.04,
                    "break_even_probability": 1.0 / 1.8,
                    "offered_payout_multiplier": 1.8,
                    "payout_assumption_version": "deriv-offer-gross-v1",
                    "safety_margin": 0.04,
                    "margin_version": "break-even-v1",
                },
                "metrics": {
                    "train": {"rows": 10, "accuracy": 0.5},
                    "validation": {"rows": 4, "accuracy": 0.5},
                    "test": {"rows": 4, "accuracy": 0.5},
                },
                "ablations": [],
                "trial_count": 1,
                "split_counts": {"train": 10, "validation": 4, "test": 4, "purged": 0},
                "direction_source": "bollinger_v1_deterministic",
                "gate_actions": ["DO_NOT_OPERATE", "OPERATE"],
            }
        )

    def create_legacy_candidates(self, artifact, *, status="TRIAL", metadata=None):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(DatabaseModels.create_tables_sql())
            db.executescript(
                """
                CREATE TABLE nexus_candidates (
                    id TEXT PRIMARY KEY,
                    nexus_version_id TEXT,
                    artifact_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            db.execute(
                "INSERT INTO nexus_candidates "
                "(id, artifact_hash, status, metadata) VALUES (?, ?, ?, ?)",
                (
                    f"candidate-{artifact.artifact_hash[:24]}",
                    artifact.artifact_hash,
                    status,
                    artifact.to_json() if metadata is None else metadata,
                ),
            )
            db.commit()

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

    def test_artifact_requires_the_complete_gate_only_manifest(self):
        with self.assertRaisesRegex(ValueError, "manifest"):
            CandidateArtifact.create({})
        unknown = dict(self.artifact().metadata)
        unknown["future_promotion"] = True
        with self.assertRaisesRegex(ValueError, "manifest"):
            CandidateArtifact.create(unknown)
        wrong_contract = dict(self.artifact().metadata)
        wrong_contract["contract"] = {
            "symbol": "R_75", "timeframe_seconds": 60, "duration_seconds": 58,
        }
        with self.assertRaises(ValueError):
            CandidateArtifact.create(wrong_contract)
        invalid_adx = json.loads(self.artifact().to_json())["metadata"]
        invalid_adx["indicator_configuration"]["adx"] = {"period": 0}
        with self.assertRaisesRegex(ValueError, "manifest"):
            CandidateArtifact.create(invalid_adx)
        invalid_number = json.loads(self.artifact().to_json())["metadata"]
        invalid_number["training_config"]["offered_payout_multiplier"] = "1.8"
        import hashlib
        from nexus_trade.artifacts import canonical_json
        invalid_number["configuration_hash"] = hashlib.sha256(
            canonical_json(invalid_number["training_config"]).encode("utf-8")
        ).hexdigest()
        try:
            CandidateArtifact.create(invalid_number)
        except TypeError as exc:
            self.fail(f"corrupt numeric manifest must fail as validation: {exc}")
        except ValueError:
            pass
        else:
            self.fail("corrupt numeric manifest was accepted")

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

    def test_legacy_registry_upgrade_freezes_status_and_survives_restart(self):
        artifact = self.artifact()
        self.create_legacy_candidates(artifact)
        first = CandidateRegistry(self.db_path)
        second = CandidateRegistry(self.db_path)
        self.assertEqual(first.list_candidates(), second.list_candidates())
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE nexus_candidates SET status = 'CHAMPION' WHERE artifact_hash = ?",
                    (artifact.artifact_hash,),
                )

    def test_legacy_registry_fails_closed_on_invalid_or_corrupt_rows(self):
        for status, metadata in (("CHAMPION", None), ("TRIAL", "{}")):
            with self.subTest(status=status, metadata=metadata):
                with tempfile.TemporaryDirectory() as directory:
                    self.db_path = os.path.join(directory, "legacy.db")
                    artifact = self.artifact()
                    self.create_legacy_candidates(artifact, status=status, metadata=metadata)
                    with self.assertRaisesRegex(ValueError, "registry"):
                        CandidateRegistry(self.db_path)


if __name__ == "__main__":
    unittest.main()
