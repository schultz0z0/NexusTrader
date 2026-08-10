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
        rebuilt = type(self).create(_plain(self.metadata))
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
]
