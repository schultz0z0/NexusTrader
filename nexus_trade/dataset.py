"""Causal immutable datasets built from durable NexusTrade settlements."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from nexus_trade.artifacts import canonical_json
from nexus_trade.constants import NEXUS_SYMBOL, NEXUS_TIMEFRAME_SECONDS


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DIRECTION_FEATURES = frozenset({"contract_type", "direction", "signal_direction"})


class DatasetRejectedError(ValueError):
    """Raised instead of coercing an unsafe learning row."""


@dataclass(frozen=True)
class LearningRow:
    contract_id: int
    feature_epoch: int
    entry_epoch: int
    label_epoch: int
    contract_type: str
    features: Mapping[str, float]
    label: int
    stake: float
    payout: float

    def canonical(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "feature_epoch": self.feature_epoch,
            "entry_epoch": self.entry_epoch,
            "label_epoch": self.label_epoch,
            "contract_type": self.contract_type,
            "features": dict(self.features),
            "label": self.label,
            "stake": self.stake,
            "payout": self.payout,
        }


@dataclass(frozen=True)
class DatasetSplit:
    name: str
    rows: tuple[LearningRow, ...]

    @property
    def feature_epoch(self) -> tuple[int, ...]:
        return tuple(row.feature_epoch for row in self.rows)

    @property
    def label_epoch(self) -> tuple[int, ...]:
        return tuple(row.label_epoch for row in self.rows)


@dataclass(frozen=True)
class LearningDataset:
    train: DatasetSplit
    validation: DatasetSplit
    test: DatasetSplit
    feature_schema: tuple[str, ...]
    dataset_hash: str
    provenance_hash: str
    cutoff_epoch: int
    purged_count: int

    @property
    def rows(self) -> tuple[LearningRow, ...]:
        return self.train.rows + self.validation.rows + self.test.rows


class DatasetBuilder:
    """Validate settled rows and produce fixed chronological purged partitions."""

    def __init__(
        self,
        settled_contracts: Iterable[Mapping],
        *,
        expected_provenance_hash: str,
        minimum_rows: int = 30,
        purge_seconds: int = NEXUS_TIMEFRAME_SECONDS,
        train_fraction: float = 0.6,
        validation_fraction: float = 0.2,
    ):
        self._source = tuple(settled_contracts)
        if not _HASH_RE.fullmatch(expected_provenance_hash or ""):
            raise ValueError("expected_provenance_hash must be a SHA-256 hex digest")
        if type(minimum_rows) is not int or minimum_rows < 6:
            raise ValueError("minimum_rows must be an integer of at least 6")
        if type(purge_seconds) is not int or purge_seconds < 0:
            raise ValueError("purge_seconds must be a non-negative integer")
        if not (0 < train_fraction < 1 and 0 < validation_fraction < 1):
            raise ValueError("split fractions must be between zero and one")
        if train_fraction + validation_fraction >= 1:
            raise ValueError("train and validation fractions must leave a test split")
        self.expected_provenance_hash = expected_provenance_hash
        self.minimum_rows = minimum_rows
        self.purge_seconds = purge_seconds
        self.train_fraction = train_fraction
        self.validation_fraction = validation_fraction

    def build(self, cutoff_epoch: int) -> LearningDataset:
        if isinstance(cutoff_epoch, bool) or type(cutoff_epoch) is not int or cutoff_epoch <= 0:
            raise DatasetRejectedError("cutoff must be a positive integer epoch")
        if len(self._source) < self.minimum_rows:
            raise DatasetRejectedError("minimum settled dataset size was not met")

        rows: list[LearningRow] = []
        feature_schema: tuple[str, ...] | None = None
        seen_contract_ids: set[int] = set()
        seen_feature_epochs: set[int] = set()
        previous_feature_epoch: int | None = None
        for raw in self._source:
            row = self._validate_row(raw, cutoff_epoch)
            if row.contract_id in seen_contract_ids or row.feature_epoch in seen_feature_epochs:
                raise DatasetRejectedError("duplicate contract or feature epoch")
            if previous_feature_epoch is not None and row.feature_epoch <= previous_feature_epoch:
                raise DatasetRejectedError("rows must be strictly chronological and unordered input is rejected")
            schema = tuple(sorted(row.features))
            if feature_schema is None:
                feature_schema = schema
            elif schema != feature_schema:
                raise DatasetRejectedError("incomplete or mismatched feature schema")
            seen_contract_ids.add(row.contract_id)
            seen_feature_epochs.add(row.feature_epoch)
            previous_feature_epoch = row.feature_epoch
            rows.append(row)

        train_end = int(len(rows) * self.train_fraction)
        validation_end = int(len(rows) * (self.train_fraction + self.validation_fraction))
        train_rows = rows[:train_end]
        validation_rows = rows[train_end:validation_end]
        test_rows = rows[validation_end:]
        if min(map(len, (train_rows, validation_rows, test_rows))) < 2:
            raise DatasetRejectedError("minimum split size was not met")

        train_rows, purged_train = self._purge_before(train_rows, validation_rows[0])
        validation_rows, purged_validation = self._purge_before(validation_rows, test_rows[0])
        if min(map(len, (train_rows, validation_rows, test_rows))) < 2:
            raise DatasetRejectedError("minimum split size was not met after temporal purge")

        partitions = {
            "train": [row.canonical() for row in train_rows],
            "validation": [row.canonical() for row in validation_rows],
            "test": [row.canonical() for row in test_rows],
        }
        identity = {
            "schema_version": 1,
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "cutoff_epoch_exclusive": cutoff_epoch,
            "provenance_hash": self.expected_provenance_hash,
            "feature_schema": list(feature_schema or ()),
            "purge_seconds": self.purge_seconds,
            "partitions": partitions,
        }
        dataset_hash = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        return LearningDataset(
            train=DatasetSplit("train", tuple(train_rows)),
            validation=DatasetSplit("validation", tuple(validation_rows)),
            test=DatasetSplit("test", tuple(test_rows)),
            feature_schema=feature_schema or (),
            dataset_hash=dataset_hash,
            provenance_hash=self.expected_provenance_hash,
            cutoff_epoch=cutoff_epoch,
            purged_count=purged_train + purged_validation,
        )

    def _purge_before(
        self, earlier: list[LearningRow], later_first: LearningRow,
    ) -> tuple[list[LearningRow], int]:
        boundary = later_first.feature_epoch - self.purge_seconds
        retained = [row for row in earlier if row.label_epoch < boundary]
        return retained, len(earlier) - len(retained)

    def _validate_row(self, raw: Mapping, cutoff_epoch: int) -> LearningRow:
        if not isinstance(raw, Mapping):
            raise DatasetRejectedError("dataset rows must be mappings")
        required = {
            "contract_id", "symbol", "timeframe_seconds", "feature_epoch",
            "entry_epoch", "label_epoch", "settled", "status", "contract_type",
            "provenance_hash", "features", "label", "stake", "payout",
        }
        missing = required - set(raw)
        if missing:
            raise DatasetRejectedError("incomplete settled row")
        contract_id = self._exact_positive_int(raw["contract_id"], "contract_id")
        feature_epoch = self._exact_positive_int(raw["feature_epoch"], "feature_epoch")
        entry_epoch = self._exact_positive_int(raw["entry_epoch"], "entry_epoch")
        label_epoch = self._exact_positive_int(raw["label_epoch"], "label_epoch")
        if raw["symbol"] != NEXUS_SYMBOL:
            raise DatasetRejectedError("cross-symbol rows are forbidden")
        if raw["timeframe_seconds"] != NEXUS_TIMEFRAME_SECONDS:
            raise DatasetRejectedError("only the R_100 M1 contract is accepted")
        if feature_epoch % NEXUS_TIMEFRAME_SECONDS or entry_epoch % NEXUS_TIMEFRAME_SECONDS:
            raise DatasetRejectedError("feature and entry epochs must align to M1")
        if not feature_epoch < entry_epoch < label_epoch:
            raise DatasetRejectedError("features must precede entry and labels must follow it")
        if label_epoch >= cutoff_epoch:
            raise DatasetRejectedError("settlement label must be strictly before cutoff")
        if raw["settled"] is not True or raw["status"] != "closed":
            raise DatasetRejectedError("only durably settled closed contracts are accepted")
        if raw["contract_type"] not in {"CALL", "PUT"}:
            raise DatasetRejectedError("Bollinger direction must be CALL or PUT")
        if raw["provenance_hash"] != self.expected_provenance_hash:
            raise DatasetRejectedError("row provenance does not match the dataset")
        if raw["label"] not in (0, 1) or type(raw["label"]) is not int:
            raise DatasetRejectedError("label must be an exact binary outcome")
        stake = self._finite_positive(raw["stake"], "stake")
        payout = self._finite_positive(raw["payout"], "payout")
        if payout <= stake:
            raise DatasetRejectedError("winning payout must exceed stake")
        features = raw["features"]
        if not isinstance(features, Mapping) or not features:
            raise DatasetRejectedError("feature values are incomplete")
        if _DIRECTION_FEATURES.intersection(str(key).lower() for key in features):
            raise DatasetRejectedError("direction cannot be a learned feature")
        normalized_features = {}
        for key, value in features.items():
            if type(key) is not str or not key:
                raise DatasetRejectedError("feature names must be non-empty strings")
            normalized_features[key] = self._finite(value, f"feature {key}")
        return LearningRow(
            contract_id=contract_id,
            feature_epoch=feature_epoch,
            entry_epoch=entry_epoch,
            label_epoch=label_epoch,
            contract_type=raw["contract_type"],
            features=MappingProxyType(normalized_features),
            label=raw["label"],
            stake=stake,
            payout=payout,
        )

    @staticmethod
    def _exact_positive_int(value, name: str) -> int:
        if isinstance(value, bool) or type(value) is not int or value <= 0:
            raise DatasetRejectedError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _finite(value, name: str) -> float:
        if isinstance(value, bool):
            raise DatasetRejectedError(f"{name} must be a finite number")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise DatasetRejectedError(f"{name} must be a finite number") from exc
        if not math.isfinite(result):
            raise DatasetRejectedError(f"{name} must be a finite number")
        return result

    @classmethod
    def _finite_positive(cls, value, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0:
            raise DatasetRejectedError(f"{name} must be positive")
        return result


__all__ = [
    "DatasetBuilder",
    "DatasetRejectedError",
    "DatasetSplit",
    "LearningDataset",
    "LearningRow",
]
