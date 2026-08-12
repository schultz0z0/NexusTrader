"""Atomic registry for immutable NexusTrade shadow candidates."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager

from database.models import DatabaseModels
from database.nexus_models import NexusModels
from nexus_trade.artifacts import (
    ArtifactIntegrityError,
    CandidateArtifact,
    canonical_json,
)


class CandidateRegistryIntegrityError(ValueError):
    """Raised when restart discovers an unsafe legacy candidate registry."""


BASELINE_CANDIDATE_ID = "candidate-nexus-trial-v1"


def deterministic_baseline_candidate(version_id: str, version_hash: str) -> dict:
    if type(version_id) is not str or not version_id:
        raise ValueError("baseline version_id is required")
    if type(version_hash) is not str or len(version_hash) != 64:
        raise ValueError("baseline version_hash must be a SHA-256 digest")
    metadata = {
        "schema_version": 1,
        "artifact_type": "nexus_trade_deterministic_baseline",
        "version_id": version_id,
        "version_hash": version_hash,
        "contract": {
            "symbol": "R_100",
            "timeframe_seconds": 60,
            "duration_seconds": 58,
        },
        "indicator_configuration": {
            "bollinger": {"period": 20, "std_dev": 2.0, "ma": "SMA"},
            "adx": {"period": 14, "max_entry": 22.0},
        },
        "direction_source": "bollinger_v1_deterministic",
        "gate": "deterministic_rules_only",
    }
    encoded = canonical_json(metadata)
    artifact_hash = hashlib.sha256(
        b"nexus-deterministic-baseline-v1\0" + encoded.encode("utf-8")
    ).hexdigest()
    return {
        "id": BASELINE_CANDIDATE_ID,
        "nexus_version_id": version_id,
        "artifact_hash": artifact_hash,
        "metadata_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "metadata": metadata,
        "encoded": encoded,
    }


class CandidateRegistry:
    """Freeze the first candidate as Trial and every later one as SHADOW.

    This registry intentionally never writes ``nexus_versions`` or ``nexus_runtime``;
    promotion and Champion mutation belong to later governed tasks.
    """

    def __init__(self, db_path: str):
        if type(db_path) is not str or not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        with self._connection() as db:
            db.executescript(DatabaseModels.create_tables_sql())
            db.executescript(NexusModels.create_tables_sql())
        self._validate_existing_registry()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def register(self, artifact: CandidateArtifact) -> dict:
        if type(artifact) is not CandidateArtifact:
            raise TypeError("artifact must be a CandidateArtifact")
        artifact.verify()
        envelope = artifact.to_json()
        candidate_id = f"candidate-{artifact.artifact_hash[:24]}"
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT * FROM nexus_candidates WHERE artifact_hash = ?",
                    (artifact.artifact_hash,),
                ).fetchone()
                if existing is not None:
                    stored = CandidateArtifact.from_json(existing["metadata"])
                    if (
                        existing["id"] != candidate_id
                        or stored.artifact_hash != artifact.artifact_hash
                        or stored.metadata_hash != artifact.metadata_hash
                    ):
                        raise ArtifactIntegrityError(
                            "registered candidate conflicts with content address"
                        )
                    db.commit()
                    return self._decode(existing)

                active_trial = db.execute(
                    "SELECT id FROM nexus_candidates WHERE status = 'TRIAL' LIMIT 1"
                ).fetchone()
                status = "SHADOW" if active_trial is not None else "TRIAL"
                db.execute(
                    """
                    INSERT INTO nexus_candidates (
                        id, nexus_version_id, artifact_hash, status, metadata
                    ) VALUES (?, NULL, ?, ?, ?)
                    """,
                    (candidate_id, artifact.artifact_hash, status, envelope),
                )
                row = db.execute(
                    "SELECT * FROM nexus_candidates WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
                db.commit()
                return self._decode(row)
            except BaseException:
                db.rollback()
                raise

    def _validate_existing_registry(self) -> None:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM nexus_candidates ORDER BY created_at, id"
            ).fetchall()
        if not rows:
            return
        if sum(row["status"] == "TRIAL" for row in rows) != 1:
            raise CandidateRegistryIntegrityError(
                "candidate registry must contain exactly one frozen Trial"
            )
        for row in rows:
            if row["status"] not in {"TRIAL", "SHADOW"}:
                raise CandidateRegistryIntegrityError(
                    "candidate registry contains an invalid legacy status"
                )
            if row["id"] == BASELINE_CANDIDATE_ID:
                self._decode_baseline(row)
            else:
                try:
                    artifact = CandidateArtifact.from_json(row["metadata"])
                except (ArtifactIntegrityError, TypeError, ValueError) as exc:
                    raise CandidateRegistryIntegrityError(
                        "candidate registry contains corrupt artifact metadata"
                    ) from exc
                expected_id = f"candidate-{artifact.artifact_hash[:24]}"
                if (
                    row["id"] != expected_id
                    or row["artifact_hash"] != artifact.artifact_hash
                    or row["nexus_version_id"] is not None
                ):
                    raise CandidateRegistryIntegrityError(
                        "candidate registry identity does not match its artifact"
                    )

    def list_candidates(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM nexus_candidates ORDER BY created_at, id"
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        if row["id"] == BASELINE_CANDIDATE_ID:
            return CandidateRegistry._decode_baseline(row)
        artifact = CandidateArtifact.from_json(row["metadata"])
        return {
            "id": row["id"],
            "nexus_version_id": row["nexus_version_id"],
            "artifact_hash": row["artifact_hash"],
            "metadata_hash": artifact.metadata_hash,
            "status": row["status"],
            "metadata": json.loads(canonical_json(artifact.metadata)),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _decode_baseline(row: sqlite3.Row) -> dict:
        try:
            metadata = json.loads(row["metadata"])
            expected = deterministic_baseline_candidate(
                row["nexus_version_id"], metadata["version_hash"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CandidateRegistryIntegrityError(
                "candidate registry contains corrupt deterministic baseline"
            ) from exc
        if (
            row["id"] != expected["id"]
            or row["artifact_hash"] != expected["artifact_hash"]
            or row["metadata"] != expected["encoded"]
        ):
            raise CandidateRegistryIntegrityError(
                "deterministic baseline identity does not match its content"
            )
        return {
            "id": row["id"],
            "nexus_version_id": row["nexus_version_id"],
            "artifact_hash": row["artifact_hash"],
            "metadata_hash": expected["metadata_hash"],
            "status": row["status"],
            "metadata": metadata,
            "created_at": row["created_at"],
        }


__all__ = [
    "BASELINE_CANDIDATE_ID",
    "CandidateRegistry",
    "CandidateRegistryIntegrityError",
    "deterministic_baseline_candidate",
]
