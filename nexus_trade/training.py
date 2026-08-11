"""Deterministic fail-closed NexusTrade learning gate training."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier

from database.models import DatabaseModels
from database.nexus_models import NexusModels
from nexus_trade.artifacts import (
    CandidateArtifact,
    canonical_json,
    serialize_fitted_hgb,
    validate_safe_json,
)
from nexus_trade.dataset import DatasetSplit, LearningDataset


class TrainingRejectedError(ValueError):
    """Raised after a fail-closed training attempt has been durably ledgered."""


@dataclass(frozen=True)
class TrainingConfig:
    seed: int
    offered_payout_multiplier: float = 1.8
    payout_assumption_version: str = "deriv-offer-gross-v1"
    safety_margin: float = 0.03
    margin_version: str = "break-even-v1"
    minimum_train_rows: int = 20
    max_iter: int = 100
    learning_rate: float = 0.1
    max_leaf_nodes: int = 15

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        for name in ("offered_payout_multiplier", "safety_margin", "learning_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be finite")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.offered_payout_multiplier <= 1:
            raise ValueError("offered_payout_multiplier must exceed one")
        if not 0 <= self.safety_margin < 1:
            raise ValueError("safety_margin must be in [0, 1)")
        if 1.0 / self.offered_payout_multiplier + self.safety_margin >= 1:
            raise ValueError("economic operate threshold must remain below one")
        if (
            type(self.payout_assumption_version) is not str
            or not self.payout_assumption_version
        ):
            raise ValueError("payout_assumption_version is required")
        if type(self.margin_version) is not str or not self.margin_version:
            raise ValueError("margin_version is required")
        if type(self.minimum_train_rows) is not int or self.minimum_train_rows < 2:
            raise ValueError("minimum_train_rows must be at least two")
        if type(self.max_iter) is not int or self.max_iter <= 0:
            raise ValueError("max_iter must be positive")
        if type(self.max_leaf_nodes) is not int or self.max_leaf_nodes < 2:
            raise ValueError("max_leaf_nodes must be at least two")

    def canonical(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "offered_payout_multiplier": float(self.offered_payout_multiplier),
            "payout_assumption_version": self.payout_assumption_version,
            "safety_margin": float(self.safety_margin),
            "margin_version": self.margin_version,
            "minimum_train_rows": self.minimum_train_rows,
            "max_iter": self.max_iter,
            "learning_rate": float(self.learning_rate),
            "max_leaf_nodes": self.max_leaf_nodes,
        }


class SQLiteTrialLedger:
    """Append-only local ledger. Repeated attempts deliberately remain repeated."""

    def __init__(self, db_path: str):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        with self._connection() as db:
            db.executescript(DatabaseModels.create_tables_sql())
            db.executescript(NexusModels.create_tables_sql())

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30.0)
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def record(self, attempt: dict[str, Any]) -> int:
        payload = dict(attempt)
        status = payload.get("status")
        if status not in {"SUCCEEDED", "REJECTED", "FAILED"}:
            raise ValueError("attempt status is invalid")
        # Ledger payloads use the same non-secret finite JSON boundary as artifacts.
        validate_safe_json({"schema_version": 1, "attempt": payload})
        encoded = canonical_json(payload)
        attempt_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                INSERT INTO nexus_training_attempts (
                    attempt_hash, status, dataset_hash, provenance_hash, seed, payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_hash,
                    status,
                    payload.get("dataset_hash"),
                    payload.get("provenance_hash"),
                    payload.get("seed"),
                    encoded,
                ),
            )
            db.commit()
            return int(cursor.lastrowid)

    def list_attempts(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, attempt_hash, payload, created_at "
                "FROM nexus_training_attempts ORDER BY id"
            ).fetchall()
        result = []
        for attempt_id, attempt_hash, raw_payload, created_at in rows:
            payload = json.loads(raw_payload)
            payload["id"] = attempt_id
            payload["attempt_hash"] = attempt_hash
            payload["created_at"] = created_at
            result.append(payload)
        return result


class Trainer:
    """Fit only the OPERATE/DO_NOT_OPERATE gate; Bollinger owns direction."""

    def __init__(self, config: TrainingConfig):
        if type(config) is not TrainingConfig:
            raise TypeError("config must be TrainingConfig")
        self.config = config

    def fit(
        self, dataset: LearningDataset, trial_ledger: SQLiteTrialLedger,
    ) -> CandidateArtifact:
        if type(dataset) is not LearningDataset:
            raise TypeError("dataset must be a LearningDataset")
        if not hasattr(trial_ledger, "record"):
            raise TypeError("trial_ledger must support record(attempt)")
        base = {
            "schema_version": 1,
            "dataset_hash": dataset.dataset_hash,
            "provenance_hash": dataset.provenance_hash,
            "seed": self.config.seed,
            "configuration": self.config.canonical(),
            "indicator_configuration": {
                "bollinger": {"period": 20, "std_dev": 2.0, "ma": "SMA"},
                "direction_contract": "bollinger_v1_deterministic",
            },
            "feature_schema": list(dataset.feature_schema),
            "result_action": "DO_NOT_OPERATE",
        }
        try:
            self._validate(dataset)
            artifact = self._fit_validated(dataset)
        except TrainingRejectedError as exc:
            trial_ledger.record(
                {
                    **base,
                    "status": "REJECTED",
                    "error_code": self._error_code(exc),
                    "trial_count": 0,
                    "metrics": {},
                    "ablations": [],
                }
            )
            raise
        except Exception as exc:
            trial_ledger.record(
                {
                    **base,
                    "status": "FAILED",
                    "error_code": "model_fit_failed",
                    "trial_count": 0,
                    "metrics": {},
                    "ablations": [],
                }
            )
            raise TrainingRejectedError("training failed closed") from exc

        metadata = artifact.metadata
        trial_ledger.record(
            {
                **base,
                "status": "SUCCEEDED",
                "result_action": "OPERATE",
                "artifact_hash": artifact.artifact_hash,
                "trial_count": metadata["trial_count"],
                "metrics": dict(metadata["metrics"]),
                "ablations": [dict(item) for item in metadata["ablations"]],
            }
        )
        return artifact

    def _validate(self, dataset: LearningDataset) -> None:
        if len(dataset.train.rows) < self.config.minimum_train_rows:
            raise TrainingRejectedError("tiny training dataset")
        labels = {row.label for row in dataset.train.rows}
        if len(labels) != 2:
            raise TrainingRejectedError("training dataset has one class")
        forbidden = {"contract_type", "direction", "signal_direction"}
        if forbidden.intersection(name.lower() for name in dataset.feature_schema):
            raise TrainingRejectedError("direction cannot be learned")

    def _fit_validated(self, dataset: LearningDataset) -> CandidateArtifact:
        break_even_probability = 1.0 / self.config.offered_payout_multiplier
        threshold = break_even_probability + self.config.safety_margin
        model = self._new_model()
        train_x, train_y = self._matrix(dataset.train, dataset.feature_schema)
        model.fit(train_x, train_y)
        metrics = {
            split.name: self._metrics(model, split, dataset.feature_schema, threshold)
            for split in (dataset.train, dataset.validation, dataset.test)
        }
        baseline_validation = metrics["validation"]["brier_score"]
        ablations = []
        for omitted in dataset.feature_schema:
            schema = tuple(name for name in dataset.feature_schema if name != omitted)
            if not schema:
                ablations.append(
                    {"omitted_feature": omitted, "validation_brier_score": None, "delta": None}
                )
                continue
            ablated_model = self._new_model()
            ablated_x, ablated_y = self._matrix(dataset.train, schema)
            ablated_model.fit(ablated_x, ablated_y)
            score = self._metrics(
                ablated_model, dataset.validation, schema, threshold,
            )["brier_score"]
            ablations.append(
                {
                    "omitted_feature": omitted,
                    "validation_brier_score": score,
                    "delta": score - baseline_validation,
                }
            )
        training_config = self.config.canonical()
        configuration_hash = hashlib.sha256(
            canonical_json(training_config).encode("utf-8")
        ).hexdigest()
        metadata = {
            "schema_version": 2,
            "artifact_type": "nexus_trade_shadow_candidate",
            "contract": {
                "symbol": "R_100",
                "timeframe_seconds": 60,
                "duration_seconds": 58,
            },
            "dataset_hash": dataset.dataset_hash,
            "provenance_hash": dataset.provenance_hash,
            "configuration_hash": configuration_hash,
            "training_config": training_config,
            "seed": self.config.seed,
            "model": {
                "family": "HistGradientBoostingClassifier",
                "library": "scikit-learn",
                "library_version": sklearn.__version__,
                "inputs": list(dataset.feature_schema),
                "output": "win_probability",
                "parameters": {
                    "random_state": self.config.seed,
                    "early_stopping": False,
                    "max_iter": self.config.max_iter,
                    "learning_rate": float(self.config.learning_rate),
                    "max_leaf_nodes": self.config.max_leaf_nodes,
                },
                "serialization": "hgb_tree_json_v1",
            },
            "fitted_model": serialize_fitted_hgb(
                model, feature_count=len(dataset.feature_schema),
            ),
            "indicator_configuration": {
                "bollinger": {"period": 20, "std_dev": 2.0, "ma": "SMA"},
                "adx": {"period": 14},
                "direction_contract": "bollinger_v1_deterministic",
            },
            "feature_schema": list(dataset.feature_schema),
            "direction_source": "bollinger_v1_deterministic",
            "gate_actions": ["DO_NOT_OPERATE", "OPERATE"],
            "operate_threshold": {
                "value": threshold,
                "break_even_probability": break_even_probability,
                "offered_payout_multiplier": float(
                    self.config.offered_payout_multiplier
                ),
                "payout_assumption_version": self.config.payout_assumption_version,
                "safety_margin": float(self.config.safety_margin),
                "margin_version": self.config.margin_version,
            },
            "metrics": metrics,
            "ablations": ablations,
            "trial_count": 1 + len(dataset.feature_schema),
            "split_counts": {
                "train": len(dataset.train.rows),
                "validation": len(dataset.validation.rows),
                "test": len(dataset.test.rows),
                "purged": dataset.purged_count,
            },
        }
        return CandidateArtifact.create(metadata)

    def _new_model(self) -> HistGradientBoostingClassifier:
        return HistGradientBoostingClassifier(
            random_state=self.config.seed,
            early_stopping=False,
            max_iter=self.config.max_iter,
            learning_rate=self.config.learning_rate,
            max_leaf_nodes=self.config.max_leaf_nodes,
        )

    @staticmethod
    def _matrix(split: DatasetSplit, schema: tuple[str, ...]):
        features = np.asarray(
            [[row.features[name] for name in schema] for row in split.rows],
            dtype=np.float64,
        )
        labels = np.asarray([row.label for row in split.rows], dtype=np.int64)
        return features, labels

    @classmethod
    def _metrics(
        cls,
        model: HistGradientBoostingClassifier,
        split: DatasetSplit,
        schema: tuple[str, ...],
        threshold: float,
    ) -> dict[str, float | int]:
        features, labels = cls._matrix(split, schema)
        probabilities = model.predict_proba(features)[:, 1]
        operated = probabilities >= threshold
        predictions = probabilities >= 0.5
        return {
            "rows": int(labels.size),
            "accuracy": float(np.mean(predictions == labels)),
            "brier_score": float(np.mean((probabilities - labels) ** 2)),
            "operate_count": int(np.sum(operated)),
            "operate_rate": float(np.mean(operated)),
        }

    @staticmethod
    def _error_code(error: TrainingRejectedError) -> str:
        message = str(error)
        if "one class" in message:
            return "one_class"
        if "tiny" in message:
            return "tiny_dataset"
        return "invalid_dataset"


__all__ = [
    "SQLiteTrialLedger",
    "Trainer",
    "TrainingConfig",
    "TrainingRejectedError",
]
