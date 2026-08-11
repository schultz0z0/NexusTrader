"""Safe immutable JSON artifacts for NexusTrade learning candidates."""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


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
_MANIFEST_OPTIONAL = frozenset({"candidate_name", "fitted_model"})
_MAX_FEATURES = 128
_MAX_TREES = 10_000
_MAX_NODES_PER_TREE = 65_535
_MAX_ABS_NODE_VALUE = 1_000_000.0
_MAX_ABS_THRESHOLD = 1e100
RUNTIME_GATE_FEATURES = frozenset({
    "adx", "bollinger_percent_b", "bollinger_z_score", "bollinger_width",
    "bollinger_slope", "adx_pdi", "adx_mdi", "chop", "atr", "atrp",
    "rsi", "stoch_k", "stoch_d", "cci", "keltner_upper",
    "keltner_center", "keltner_lower", "roc", "aroon_up", "aroon_down",
    "sma", "ema", "wma", "hma", "kama", "body", "body_ratio",
    "upper_wick", "lower_wick", "upper_wick_ratio", "lower_wick_ratio",
})


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
    schema_version = metadata["schema_version"]
    if schema_version not in {1, 2}:
        raise _manifest_error("schema_version must be 1 or 2")
    if schema_version == 1 and "fitted_model" in metadata:
        raise _manifest_error("schema_version 1 cannot contain fitted state")
    if schema_version == 2 and "fitted_model" not in metadata:
        raise _manifest_error("schema_version 2 requires fitted state")
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
        or len(schema) > _MAX_FEATURES
        or any(type(name) is not str or not name for name in schema)
        or len(schema) != len(set(schema))
        or {"direction", "contract_type", "signal_direction"}.intersection(
            name.lower() for name in schema
        )
    ):
        raise _manifest_error("feature_schema is invalid")
    if schema_version == 2 and not set(schema).issubset(RUNTIME_GATE_FEATURES):
        raise _manifest_error("feature_schema contains unsupported runtime features")
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
        or model["serialization"] != (
            "retrain_from_content_addressed_dataset"
            if schema_version == 1 else "hgb_tree_json_v1"
        )
    ):
        raise _manifest_error("model must be the reproducible gate-only classifier")
    if schema_version == 2:
        _validate_fitted_model(
            metadata["fitted_model"],
            feature_count=len(schema),
            max_iter=config["max_iter"],
            max_leaf_nodes=config["max_leaf_nodes"],
        )

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
        or not 0.0 < threshold["value"] <= 1.0
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


def _validate_fitted_model(
    state: Any,
    *,
    feature_count: int,
    max_iter: int,
    max_leaf_nodes: int,
) -> None:
    required = {
        "schema_version", "family", "link", "n_features", "classes",
        "baseline", "trees",
    }
    if not isinstance(state, dict) or set(state) != required:
        raise _manifest_error("fitted_model fields are invalid")
    if (
        state["schema_version"] != 1
        or state["family"] != "HistGradientBoostingClassifier"
        or state["link"] != "logit"
        or type(state["n_features"]) is not int
        or state["n_features"] != feature_count
        or state["classes"] != [0, 1]
    ):
        raise _manifest_error("fitted_model contract is invalid")
    baseline = state["baseline"]
    if (
        isinstance(baseline, bool)
        or not isinstance(baseline, (int, float))
        or not math.isfinite(float(baseline))
        or abs(float(baseline)) > _MAX_ABS_NODE_VALUE
    ):
        raise _manifest_error("fitted_model baseline is invalid")
    trees = state["trees"]
    if (
        not isinstance(trees, list)
        or not trees
        or len(trees) > _MAX_TREES
        or len(trees) != max_iter
    ):
        raise _manifest_error("fitted_model tree count is invalid")
    max_nodes = min(_MAX_NODES_PER_TREE, 2 * max_leaf_nodes - 1)
    for tree in trees:
        _validate_tree(tree, feature_count=feature_count, max_nodes=max_nodes)


def _validate_tree(tree: Any, *, feature_count: int, max_nodes: int) -> None:
    node_fields = {
        "value", "feature_index", "threshold", "missing_go_to_left",
        "left", "right", "is_leaf",
    }
    if not isinstance(tree, list) or not tree or len(tree) > max_nodes:
        raise _manifest_error("fitted_model tree size is invalid")
    for node in tree:
        if not isinstance(node, dict) or set(node) != node_fields:
            raise _manifest_error("fitted_model node fields are invalid")
        if type(node["is_leaf"]) is not bool or type(node["missing_go_to_left"]) is not bool:
            raise _manifest_error("fitted_model node flags are invalid")
        for field, limit in (("value", _MAX_ABS_NODE_VALUE), ("threshold", _MAX_ABS_THRESHOLD)):
            value = node[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or abs(float(value)) > limit
            ):
                raise _manifest_error(f"fitted_model node {field} is invalid")
        for field in ("feature_index", "left", "right"):
            if type(node[field]) is not int or node[field] < 0:
                raise _manifest_error(f"fitted_model node {field} is invalid")
        if node["is_leaf"]:
            if node["left"] != 0 or node["right"] != 0:
                raise _manifest_error("fitted_model leaf children are invalid")
        elif (
            node["feature_index"] >= feature_count
            or node["left"] >= len(tree)
            or node["right"] >= len(tree)
            or node["left"] == node["right"]
        ):
            raise _manifest_error("fitted_model split is invalid")

    visited: set[int] = set()
    pending = [0]
    while pending:
        index = pending.pop()
        if index in visited:
            raise _manifest_error("fitted_model tree contains a cycle")
        visited.add(index)
        node = tree[index]
        if not node["is_leaf"]:
            pending.extend((node["left"], node["right"]))
    if len(visited) != len(tree):
        raise _manifest_error("fitted_model tree contains unreachable nodes")


def serialize_fitted_hgb(model: Any, *, feature_count: int) -> dict[str, Any]:
    """Convert the supported numeric binary HGB subset into inert JSON data."""
    if type(feature_count) is not int or not 0 < feature_count <= _MAX_FEATURES:
        raise ValueError("feature_count is invalid")
    classes = getattr(model, "classes_", None)
    predictors = getattr(model, "_predictors", None)
    baseline = getattr(model, "_baseline_prediction", None)
    if (
        classes is None
        or list(classes) != [0, 1]
        or getattr(model, "n_features_in_", None) != feature_count
        or getattr(model, "n_trees_per_iteration_", None) != 1
        or getattr(model, "is_categorical_", None) is not None
        or predictors is None
        or baseline is None
    ):
        raise ValueError("fitted HGB model uses an unsupported executable contract")
    trees = []
    for iteration in predictors:
        if len(iteration) != 1:
            raise ValueError("fitted HGB model must have one binary tree per iteration")
        predictor = iteration[0]
        if getattr(predictor, "raw_left_cat_bitsets", ()).size:
            raise ValueError("categorical HGB trees are unsupported")
        nodes = []
        for raw in predictor.nodes:
            if bool(raw["is_categorical"]):
                raise ValueError("categorical HGB nodes are unsupported")
            nodes.append({
                "value": float(raw["value"]),
                "feature_index": int(raw["feature_idx"]),
                "threshold": float(raw["num_threshold"]),
                "missing_go_to_left": bool(raw["missing_go_to_left"]),
                "left": int(raw["left"]),
                "right": int(raw["right"]),
                "is_leaf": bool(raw["is_leaf"]),
            })
        trees.append(nodes)
    return {
        "schema_version": 1,
        "family": "HistGradientBoostingClassifier",
        "link": "logit",
        "n_features": feature_count,
        "classes": [0, 1],
        "baseline": float(baseline[0][0]),
        "trees": trees,
    }


@dataclass(frozen=True)
class ExecutableHGBGate:
    """Validated pure-Python inference for one immutable candidate artifact."""

    feature_schema: tuple[str, ...]
    operate_threshold: float
    baseline: float
    trees: tuple[tuple[Mapping[str, Any], ...], ...]
    artifact_hash: str

    def predict_probability(self, values: Sequence[Any]) -> float:
        if isinstance(values, (str, bytes)) or len(values) != len(self.feature_schema):
            raise ValueError("gate feature vector does not match feature_schema")
        features: list[float] = []
        missing: list[bool] = []
        for value in values:
            if value is None:
                features.append(0.0)
                missing.append(True)
                continue
            if isinstance(value, bool):
                raise ValueError("gate features must be numeric or missing")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("gate features must be numeric or missing") from exc
            if math.isinf(number):
                raise ValueError("gate features cannot be infinite")
            features.append(0.0 if math.isnan(number) else number)
            missing.append(math.isnan(number))

        raw_score = self.baseline
        for tree in self.trees:
            index = 0
            while not tree[index]["is_leaf"]:
                node = tree[index]
                feature_index = node["feature_index"]
                go_left = (
                    node["missing_go_to_left"]
                    if missing[feature_index]
                    else features[feature_index] <= node["threshold"]
                )
                index = node["left"] if go_left else node["right"]
            raw_score += tree[index]["value"]
        if not math.isfinite(raw_score):
            raise ValueError("gate produced a non-finite score")
        if raw_score >= 0.0:
            probability = 1.0 / (1.0 + math.exp(-raw_score))
        else:
            exp_score = math.exp(raw_score)
            probability = exp_score / (1.0 + exp_score)
        if not 0.0 <= probability <= 1.0 or not math.isfinite(probability):
            raise ValueError("gate produced an invalid probability")
        return probability

    def should_operate(self, values: Sequence[Any]) -> bool:
        return self.predict_probability(values) >= self.operate_threshold


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
    """A content-addressed candidate containing only inert validated JSON."""

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
        domain = (
            b"nexus-candidate-json-v2\0"
            if plain["schema_version"] == 2
            else b"nexus-candidate-json-v1\0"
        )
        artifact_hash = hashlib.sha256(domain + encoded).hexdigest()
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

    def executable_gate(self) -> ExecutableHGBGate:
        self.verify()
        metadata = _plain(self.metadata)
        if metadata.get("schema_version") != 2:
            raise ArtifactIntegrityError("candidate artifact is not executable")
        state = metadata["fitted_model"]
        return ExecutableHGBGate(
            feature_schema=tuple(metadata["feature_schema"]),
            operate_threshold=float(metadata["operate_threshold"]["value"]),
            baseline=float(state["baseline"]),
            trees=tuple(
                tuple(MappingProxyType(dict(node)) for node in tree)
                for tree in state["trees"]
            ),
            artifact_hash=self.artifact_hash,
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
    "ExecutableHGBGate",
    "RUNTIME_GATE_FEATURES",
    "canonical_json",
    "serialize_fitted_hgb",
    "validate_safe_json",
]
