"""Safe immutable JSON artifacts for NexusTrade learning candidates."""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_PARTS = (
    "token", "secret", "password", "account", "credential", "api_key",
    "ticket", "pickle", "path",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_REQUIRED = frozenset({
    "schema_version", "artifact_type", "contract", "dataset_hash",
    "provenance_hash", "configuration_hash", "training_config", "seed",
    "indicator_configuration", "model", "feature_schema", "operate_threshold",
    "metrics", "ablations", "trial_count", "split_counts", "direction_source",
    "gate_actions",
})
_MANIFEST_OPTIONAL = frozenset({"candidate_name"})


class ArtifactIntegrityError(ValueError):
    """Raised when a candidate envelope does not match its content hash."""


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_safe_json(value: Any, *, key_path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ValueError("artifact metadata keys must be non-empty strings")
            lowered = key.lower()
            if any(part in lowered for part in _SENSITIVE_PARTS):
                raise ValueError(f"unsafe artifact metadata field: {key}")
            _validate_safe_json(item, key_path=key_path + (key,))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_json(item, key_path=key_path)
        return
    if value is None or type(value) in (str, int, bool):
        if type(value) is str and (ntpath.isabs(value) or value.startswith("file:")):
            raise ValueError("artifact metadata cannot contain absolute local paths")
        return
    if type(value) is float and math.isfinite(value):
        return
    raise ValueError("artifact metadata must contain only finite JSON values")


def validate_safe_json(value: Any) -> None:
    """Validate generic ledger JSON without treating it as a candidate manifest."""
    _validate_safe_json(_plain(value))


def _manifest_error(message: str) -> ValueError:
    return ValueError(f"candidate manifest {message}")


def _require_sha256(value: Any, field: str) -> None:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise _manifest_error(f"{field} must be a SHA-256 digest")


def _validate_manifest(metadata: dict[str, Any]) -> None:
    fields = set(metadata)
    if not _MANIFEST_REQUIRED.issubset(fields):
        raise _manifest_error("is missing required fields")
    if fields - _MANIFEST_REQUIRED - _MANIFEST_OPTIONAL:
        raise _manifest_error("contains unknown fields")
    if metadata["schema_version"] != 1:
        raise _manifest_error("schema_version must be 1")
    if metadata["artifact_type"] != "nexus_trade_shadow_candidate":
        raise _manifest_error("artifact_type is invalid")
    if "candidate_name" in metadata and (
        type(metadata["candidate_name"]) is not str or not metadata["candidate_name"]
    ):
        raise _manifest_error("candidate_name must be non-empty")
    if metadata["contract"] != {
        "symbol": "R_100", "timeframe_seconds": 60, "duration_seconds": 58,
    }:
        raise _manifest_error("must preserve the R_100/M1/58s contract")
    for field in ("dataset_hash", "provenance_hash", "configuration_hash"):
        _require_sha256(metadata[field], field)

    config = metadata["training_config"]
    config_fields = {
        "seed", "offered_payout_multiplier", "payout_assumption_version",
        "safety_margin", "margin_version", "minimum_train_rows", "max_iter",
        "learning_rate", "max_leaf_nodes",
    }
    if not isinstance(config, dict) or set(config) != config_fields:
        raise _manifest_error("training_config fields are invalid")
    numeric_config = (
        "offered_payout_multiplier", "safety_margin", "learning_rate",
    )
    integer_config = ("minimum_train_rows", "max_iter", "max_leaf_nodes")
    if any(
        isinstance(config[name], bool)
        or not isinstance(config[name], (int, float))
        or not math.isfinite(float(config[name]))
        for name in numeric_config
    ) or any(
        isinstance(config[name], bool)
        or type(config[name]) is not int
        or config[name] <= 0
        for name in integer_config
    ):
        raise _manifest_error("training_config values are invalid")
    if (
        config["offered_payout_multiplier"] <= 1
        or not 0 <= config["safety_margin"] < 1
        or config["learning_rate"] <= 0
        or type(config["payout_assumption_version"]) is not str
        or not config["payout_assumption_version"]
        or type(config["margin_version"]) is not str
        or not config["margin_version"]
    ):
        raise _manifest_error("training_config economic assumptions are invalid")
    computed_config_hash = hashlib.sha256(
        canonical_json(config).encode("utf-8")
    ).hexdigest()
    if metadata["configuration_hash"] != computed_config_hash:
        raise _manifest_error("configuration_hash does not match training_config")
    if (
        isinstance(metadata["seed"], bool)
        or type(metadata["seed"]) is not int
        or metadata["seed"] < 0
        or metadata["seed"] != config["seed"]
    ):
        raise _manifest_error("seed is invalid or mismatched")

    indicators = metadata["indicator_configuration"]
    if not isinstance(indicators, dict) or set(indicators) != {
        "bollinger", "adx", "direction_contract",
    }:
        raise _manifest_error("indicator_configuration fields are invalid")
    if indicators["bollinger"] != {"period": 20, "std_dev": 2.0, "ma": "SMA"}:
        raise _manifest_error("requires the fixed Bollinger setup")
    if indicators["adx"] != {"period": 14}:
        raise _manifest_error("ADX configuration is invalid")
    if indicators["direction_contract"] != "bollinger_v1_deterministic":
        raise _manifest_error("requires deterministic Bollinger direction")
    if metadata["direction_source"] != "bollinger_v1_deterministic":
        raise _manifest_error("direction_source is invalid")
    if metadata["gate_actions"] != ["DO_NOT_OPERATE", "OPERATE"]:
        raise _manifest_error("gate actions must remain gate-only")

    schema = metadata["feature_schema"]
    if (
        not isinstance(schema, list)
        or not schema
        or any(type(name) is not str or not name for name in schema)
        or len(schema) != len(set(schema))
        or {"direction", "contract_type", "signal_direction"}.intersection(
            name.lower() for name in schema
        )
    ):
        raise _manifest_error("feature_schema is invalid")
    model = metadata["model"]
    required_model = {"family", "inputs", "output", "serialization"}
    optional_model = {"library", "library_version", "parameters"}
    if (
        not isinstance(model, dict)
        or not required_model.issubset(model)
        or set(model) - required_model - optional_model
        or model["family"] != "HistGradientBoostingClassifier"
        or model["inputs"] != schema
        or model["output"] != "win_probability"
        or model["serialization"] != "retrain_from_content_addressed_dataset"
    ):
        raise _manifest_error("model must be the reproducible gate-only classifier")

    threshold = metadata["operate_threshold"]
    threshold_fields = {
        "value", "break_even_probability", "offered_payout_multiplier",
        "payout_assumption_version", "safety_margin", "margin_version",
    }
    if not isinstance(threshold, dict) or set(threshold) != threshold_fields:
        raise _manifest_error("operate_threshold fields are invalid")
    multiplier = config["offered_payout_multiplier"]
    margin = config["safety_margin"]
    if any(
        isinstance(threshold[name], bool)
        or not isinstance(threshold[name], (int, float))
        or not math.isfinite(float(threshold[name]))
        for name in (
            "value", "break_even_probability", "offered_payout_multiplier",
            "safety_margin",
        )
    ):
        raise _manifest_error("economic threshold values are invalid")
    if (
        isinstance(multiplier, bool)
        or not isinstance(multiplier, (int, float))
        or multiplier <= 1
        or threshold["offered_payout_multiplier"] != multiplier
        or threshold["payout_assumption_version"] != config["payout_assumption_version"]
        or threshold["margin_version"] != config["margin_version"]
        or threshold["safety_margin"] != margin
        or not math.isclose(
            threshold["break_even_probability"], 1.0 / multiplier,
            rel_tol=0, abs_tol=1e-12,
        )
        or not math.isclose(
            threshold["value"], 1.0 / multiplier + margin,
            rel_tol=0, abs_tol=1e-12,
        )
    ):
        raise _manifest_error("economic threshold is inconsistent")

    metrics = metadata["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {"train", "validation", "test"}:
        raise _manifest_error("metrics must contain chronological partitions")
    if any(not isinstance(metrics[name], dict) or not metrics[name] for name in metrics):
        raise _manifest_error("partition metrics are incomplete")
    if not isinstance(metadata["ablations"], list):
        raise _manifest_error("ablations must be a list")
    if (
        isinstance(metadata["trial_count"], bool)
        or type(metadata["trial_count"]) is not int
        or metadata["trial_count"] != 1 + len(metadata["ablations"])
    ):
        raise _manifest_error("trial_count does not match ablations")
    split_counts = metadata["split_counts"]
    if not isinstance(split_counts, dict) or set(split_counts) != {
        "train", "validation", "test", "purged",
    }:
        raise _manifest_error("split_counts fields are invalid")
    if any(
        isinstance(value, bool) or type(value) is not int or value < 0
        for value in split_counts.values()
    ) or any(split_counts[name] == 0 for name in ("train", "validation", "test")):
        raise _manifest_error("split_counts values are invalid")


def _loads_json(payload: str) -> Any:
    if type(payload) is not str:
        raise ArtifactIntegrityError("artifact envelope must be JSON text")

    def reject_constant(value: str):
        raise ArtifactIntegrityError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(payload, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ArtifactIntegrityError("artifact envelope is invalid JSON") from exc


@dataclass(frozen=True)
class CandidateArtifact:
    """A reproducible candidate description; executable model bytes are forbidden."""

    artifact_hash: str
    metadata_hash: str
    metadata: Mapping[str, Any]

    @classmethod
    def create(cls, metadata: Mapping[str, Any]) -> "CandidateArtifact":
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        plain = _plain(metadata)
        _validate_safe_json(plain)
        _validate_manifest(plain)
        encoded = canonical_json(plain).encode("utf-8")
        metadata_hash = hashlib.sha256(encoded).hexdigest()
        artifact_hash = hashlib.sha256(b"nexus-candidate-json-v1\0" + encoded).hexdigest()
        return cls(
            artifact_hash=artifact_hash,
            metadata_hash=metadata_hash,
            metadata=_freeze(plain),
        )

    def verify(self) -> None:
        if not _HASH_RE.fullmatch(self.artifact_hash or ""):
            raise ArtifactIntegrityError("artifact hash is malformed")
        if not _HASH_RE.fullmatch(self.metadata_hash or ""):
            raise ArtifactIntegrityError("metadata hash is malformed")
        try:
            rebuilt = type(self).create(_plain(self.metadata))
        except (TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("artifact manifest is invalid") from exc
        if (
            rebuilt.artifact_hash != self.artifact_hash
            or rebuilt.metadata_hash != self.metadata_hash
        ):
            raise ArtifactIntegrityError("artifact content hash mismatch")

    def to_json(self) -> str:
        self.verify()
        return canonical_json(
            {
                "artifact_hash": self.artifact_hash,
                "metadata": _plain(self.metadata),
                "metadata_hash": self.metadata_hash,
            }
        )

    @classmethod
    def from_json(cls, payload: str) -> "CandidateArtifact":
        envelope = _loads_json(payload)
        if not isinstance(envelope, dict) or set(envelope) != {
            "artifact_hash", "metadata", "metadata_hash",
        }:
            raise ArtifactIntegrityError("artifact envelope fields are invalid")
        artifact = cls(
            artifact_hash=envelope["artifact_hash"],
            metadata_hash=envelope["metadata_hash"],
            metadata=_freeze(envelope["metadata"]),
        )
        artifact.verify()
        return artifact


__all__ = [
    "ArtifactIntegrityError",
    "CandidateArtifact",
    "canonical_json",
    "validate_safe_json",
]
