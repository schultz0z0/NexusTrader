"""Atomic registry for immutable NexusTrade shadow candidates."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from database.models import DatabaseModels
from database.nexus_models import NexusModels
from nexus_trade.artifacts import (
    ArtifactIntegrityError,
    CandidateArtifact,
    canonical_json,
)


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

    def list_candidates(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM nexus_candidates ORDER BY created_at, id"
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
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


__all__ = ["CandidateRegistry"]
