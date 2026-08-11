import hashlib
import json
from contextlib import asynccontextmanager

import aiosqlite

from database.nexus_models import NexusModels
from nexus_trade.artifacts import (
    ArtifactIntegrityError,
    CandidateArtifact,
    canonical_json,
)
from nexus_trade.constants import (
    NEXUS_DEMO_STAKE,
    NEXUS_DURATION_SECONDS,
    NEXUS_DURATION_UNIT,
    NEXUS_SYMBOL,
    NEXUS_TIMEFRAME_SECONDS,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.domain import CampaignStatus, Lane, VersionStatus


class NexusTradeSingletonError(RuntimeError):
    """Raised when the protected NexusTrade identity has been corrupted."""


class NexusTradeRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    @staticmethod
    def champion_v1_snapshot() -> dict:
        return {
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "duration_seconds": NEXUS_DURATION_SECONDS,
            "bollinger": {"period": 20, "std_dev": 2, "ma": "SMA"},
            "adx": {"period": 14, "max_entry": 22},
        }

    @classmethod
    def _v1_identity(cls, role: VersionStatus) -> tuple[str, str, str]:
        encoded = canonical_json(cls.champion_v1_snapshot())
        if role is VersionStatus.CHAMPION:
            version_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            return f"nexus-v1-{version_hash[:12]}", version_hash, encoded
        version_hash = hashlib.sha256(
            b"nexus-trial-v1\0" + encoded.encode("utf-8")
        ).hexdigest()
        return f"nexus-trial-v1-{version_hash[:12]}", version_hash, encoded

    @classmethod
    async def ensure_singleton_in_transaction(cls, db: aiosqlite.Connection) -> dict:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bot_instances WHERE id = ? OR strategy_id = 'nexus_trade'",
            (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            existing_bots = await cursor.fetchall()
        if any(row["id"] != NEXUS_TRADE_BOT_ID or row["strategy_id"] != "nexus_trade" for row in existing_bots):
            raise NexusTradeSingletonError("NexusTrade must use the protected nexus-trade identity")
        if existing_bots:
            cls._validate_canonical_bot(existing_bots[0])
        fresh = not existing_bots

        cursor = await db.execute(
            """
            INSERT INTO bot_instances (
                id, name, strategy_id, strategy_config, account_id, account_type,
                symbol, timeframe_seconds, duration, duration_unit, initial_stake,
                money_management, money_config, risk_config, desired_state, runtime_state
            ) VALUES (?, 'NexusTrade', 'nexus_trade', '{}', '', 'demo', ?, ?, ?, ?, ?, 'fixed', '{}', '{}', 'STOPPED', 'STOPPED')
            ON CONFLICT(id) DO NOTHING
            """,
            (NEXUS_TRADE_BOT_ID, NEXUS_SYMBOL, NEXUS_TIMEFRAME_SECONDS,
             NEXUS_DURATION_SECONDS, NEXUS_DURATION_UNIT, NEXUS_DEMO_STAKE),
        )
        version_id, version_hash, encoded_snapshot = cls._v1_identity(
            VersionStatus.CHAMPION,
        )
        async with db.execute(
            "SELECT * FROM nexus_versions WHERE id = ? OR version_hash = ?",
            (version_id, version_hash),
        ) as version_cursor:
            versions = await version_cursor.fetchall()
        if versions:
            if len(versions) != 1:
                raise NexusTradeSingletonError("Champion V1 identity is ambiguous")
            cls._validate_champion_v1(versions[0], version_id, version_hash, encoded_snapshot)
        else:
            await db.execute(
                """
                INSERT INTO nexus_versions (id, name, status, version_hash, snapshot)
                VALUES (?, 'Champion V1', ?, ?, ?)
                """,
                (version_id, VersionStatus.CHAMPION.value, version_hash, encoded_snapshot),
            )

        trial_id, trial_hash, trial_snapshot = cls._v1_identity(VersionStatus.TRIAL)
        async with db.execute(
            "SELECT * FROM nexus_versions WHERE id = ? OR version_hash = ?",
            (trial_id, trial_hash),
        ) as trial_cursor:
            trial_versions = await trial_cursor.fetchall()
        trial_v1_was_missing = not trial_versions
        if trial_versions:
            if len(trial_versions) != 1:
                raise NexusTradeSingletonError("Trial V1 identity is ambiguous")
            cls._validate_trial_v1(
                trial_versions[0], trial_id, trial_hash, trial_snapshot,
            )
        else:
            await db.execute(
                "INSERT INTO nexus_versions (id,name,status,version_hash,snapshot) "
                "VALUES (?, 'Trial V1', ?, ?, ?)",
                (trial_id, VersionStatus.TRIAL.value, trial_hash, trial_snapshot),
            )

        if fresh:
            await db.execute(
                "INSERT INTO nexus_runtime "
                "(bot_id, champion_version_id, trial_version_id) VALUES (?, ?, ?)",
                (NEXUS_TRADE_BOT_ID, version_id, trial_id),
            )
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,?)",
                (
                    f"trial-{trial_id}", Lane.TRIAL.value, trial_id,
                    CampaignStatus.ACTIVE.value,
                ),
            )
            await db.execute(
                "INSERT INTO nexus_campaigns (id,lane,nexus_version_id,status) "
                "VALUES (?,?,?,?)",
                (
                    f"champion-{version_id}", Lane.CHAMPION.value, version_id,
                    CampaignStatus.ACTIVE.value,
                ),
            )
        else:
            await cls._migrate_exact_legacy_v1(
                db,
                champion_version_id=version_id,
                trial_version_id=trial_id,
                trial_v1_was_missing=trial_v1_was_missing,
            )
        return await cls._snapshot_from_connection(db)

    async def ensure_singleton(self) -> dict:
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.executescript(NexusModels.create_tables_sql())
            await db.execute("BEGIN IMMEDIATE")
            try:
                snapshot = await self.ensure_singleton_in_transaction(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    def _validate_canonical_bot(bot: aiosqlite.Row) -> None:
        expected = {
            "id": NEXUS_TRADE_BOT_ID,
            "name": "NexusTrade",
            "strategy_id": "nexus_trade",
            "strategy_config": "{}",
            "account_id": "",
            "account_type": "demo",
            "symbol": NEXUS_SYMBOL,
            "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
            "duration": NEXUS_DURATION_SECONDS,
            "duration_unit": NEXUS_DURATION_UNIT,
            "initial_stake": NEXUS_DEMO_STAKE,
            "money_management": "fixed",
            "money_config": "{}",
            "risk_config": "{}",
        }
        mismatches = [key for key, value in expected.items() if bot[key] != value]
        if mismatches:
            raise NexusTradeSingletonError(
                f"NexusTrade singleton has incompatible fields: {', '.join(mismatches)}"
            )

    @staticmethod
    def _validate_champion_v1(
        version: aiosqlite.Row,
        version_id: str,
        version_hash: str,
        snapshot: str,
    ) -> None:
        expected = {
            "id": version_id,
            "name": "Champion V1",
            "status": VersionStatus.CHAMPION.value,
            "version_hash": version_hash,
            "snapshot": snapshot,
        }
        mismatches = [key for key, value in expected.items() if version[key] != value]
        if mismatches:
            raise NexusTradeSingletonError(
                f"Champion V1 has incompatible fields: {', '.join(mismatches)}"
            )

    @staticmethod
    def _validate_trial_v1(
        version: aiosqlite.Row,
        version_id: str,
        version_hash: str,
        snapshot: str,
    ) -> None:
        expected = {
            "id": version_id,
            "name": "Trial V1",
            "status": VersionStatus.TRIAL.value,
            "version_hash": version_hash,
            "snapshot": snapshot,
        }
        mismatches = [key for key, value in expected.items() if version[key] != value]
        if mismatches:
            raise NexusTradeSingletonError(
                f"Trial V1 has incompatible fields: {', '.join(mismatches)}"
            )

    @classmethod
    async def _migrate_exact_legacy_v1(
        cls,
        db: aiosqlite.Connection,
        *,
        champion_version_id: str,
        trial_version_id: str,
        trial_v1_was_missing: bool,
    ) -> None:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM nexus_runtime WHERE bot_id = ?", (NEXUS_TRADE_BOT_ID,),
        ) as cursor:
            runtime = await cursor.fetchone()
        if (
            not trial_v1_was_missing
            or runtime is None
            or runtime["trial_version_id"] != champion_version_id
        ):
            return
        async with db.execute(
            "SELECT * FROM nexus_campaigns WHERE lane = ? AND status = ?",
            (Lane.TRIAL.value, CampaignStatus.ACTIVE.value),
        ) as cursor:
            campaigns = await cursor.fetchall()
        if (
            len(campaigns) != 1
            or type(campaigns[0]["id"]) is not str
            or not campaigns[0]["id"]
            or campaigns[0]["nexus_version_id"] != champion_version_id
        ):
            return
        campaign_id = campaigns[0]["id"]
        await db.execute(
            "UPDATE nexus_runtime SET trial_version_id = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE bot_id = ?",
            (trial_version_id, NEXUS_TRADE_BOT_ID),
        )
        await db.execute(
            "UPDATE nexus_campaigns SET nexus_version_id = ? WHERE id = ?",
            (trial_version_id, campaign_id),
        )

    @classmethod
    async def _snapshot_from_connection(cls, db: aiosqlite.Connection) -> dict:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_instances WHERE id = ?", (NEXUS_TRADE_BOT_ID,)) as cursor:
            bot = await cursor.fetchone()
        if bot is None:
            raise NexusTradeSingletonError("NexusTrade singleton has not been provisioned")
        cls._validate_canonical_bot(bot)
        async with db.execute("SELECT * FROM nexus_runtime WHERE bot_id = ?", (NEXUS_TRADE_BOT_ID,)) as cursor:
            runtime = await cursor.fetchone()
        if runtime is None:
            raise NexusTradeSingletonError("NexusTrade runtime pointers are missing")

        lanes = []
        for lane, pointer in ((Lane.CHAMPION, "champion_version_id"), (Lane.TRIAL, "trial_version_id")):
            version_id = runtime[pointer]
            if type(version_id) is not str or not version_id:
                raise NexusTradeSingletonError(f"{lane.value} runtime pointer is malformed")
            async with db.execute("SELECT * FROM nexus_versions WHERE id = ?", (version_id,)) as cursor:
                version = await cursor.fetchone()
            if version is None:
                raise NexusTradeSingletonError(f"{lane.value} runtime version is missing")
            expected_status = (
                VersionStatus.CHAMPION.value
                if lane is Lane.CHAMPION else VersionStatus.TRIAL.value
            )
            if version["status"] != expected_status:
                raise NexusTradeSingletonError(
                    f"{lane.value} runtime pointer references the wrong version role"
                )
            version_data = await cls._validated_version(db, version, lane=lane)
            lanes.append({"lane": lane.value, "version": version_data})
        async with db.execute(
            "SELECT * FROM nexus_campaigns WHERE lane = ? AND status = ? ORDER BY started_at, id",
            (Lane.TRIAL.value, CampaignStatus.ACTIVE.value),
        ) as cursor:
            active_campaigns = [dict(row) for row in await cursor.fetchall()]
        if len(active_campaigns) != 1:
            raise NexusTradeSingletonError(
                "NexusTrade must have exactly one active Trial campaign"
            )
        active_trial = active_campaigns[0]
        if (
            type(active_trial.get("id")) is not str
            or not active_trial["id"]
            or active_trial.get("nexus_version_id") != runtime["trial_version_id"]
            or active_trial.get("ended_at") is not None
        ):
            raise NexusTradeSingletonError(
                "active Trial campaign does not match the runtime pointer"
            )
        return {"bot": dict(bot), "runtime": dict(runtime), "lanes": lanes, "active_campaigns": active_campaigns}

    @classmethod
    async def _validated_version(
        cls,
        db: aiosqlite.Connection,
        version: aiosqlite.Row,
        *,
        lane: Lane,
    ) -> dict:
        raw_snapshot = version["snapshot"]
        if type(raw_snapshot) is not str:
            raise NexusTradeSingletonError("NexusTrade version snapshot is malformed")
        try:
            snapshot = json.loads(
                raw_snapshot,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid constant: {value}")
                ),
            )
            encoded = canonical_json(snapshot)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NexusTradeSingletonError("NexusTrade version snapshot is malformed") from exc
        if not isinstance(snapshot, dict) or encoded != raw_snapshot:
            raise NexusTradeSingletonError("NexusTrade version snapshot is not canonical")

        champion_id, champion_hash, champion_snapshot = cls._v1_identity(
            VersionStatus.CHAMPION,
        )
        trial_id, trial_hash, trial_snapshot = cls._v1_identity(VersionStatus.TRIAL)
        if version["id"] == champion_id:
            cls._validate_champion_v1(
                version, champion_id, champion_hash, champion_snapshot,
            )
        elif version["id"] == trial_id:
            cls._validate_trial_v1(version, trial_id, trial_hash, trial_snapshot)
        else:
            expected_fields = (
                {"schema_version", "candidate_id", "artifact", "approval"}
                if lane is Lane.CHAMPION
                else {"schema_version", "candidate_id", "artifact", "trial_selection"}
            )
            if set(snapshot) != expected_fields or snapshot.get("schema_version") != 1:
                raise NexusTradeSingletonError("candidate version snapshot fields are invalid")
            try:
                artifact = CandidateArtifact.from_json(
                    canonical_json(snapshot["artifact"]),
                )
                artifact.executable_gate()
            except (ArtifactIntegrityError, TypeError, ValueError) as exc:
                raise NexusTradeSingletonError(
                    "candidate version artifact is corrupt or non-executable"
                ) from exc
            candidate_id = f"candidate-{artifact.artifact_hash[:24]}"
            if snapshot.get("candidate_id") != candidate_id:
                raise NexusTradeSingletonError("candidate version identity is invalid")
            async with db.execute(
                "SELECT artifact_hash, metadata FROM nexus_candidates WHERE id = ?",
                (candidate_id,),
            ) as cursor:
                candidate = await cursor.fetchone()
            if (
                candidate is None
                or candidate["artifact_hash"] != artifact.artifact_hash
                or candidate["metadata"] != artifact.to_json()
            ):
                raise NexusTradeSingletonError("candidate version provenance is missing")
            domain = (
                b"nexus-champion-version-v1\0"
                if lane is Lane.CHAMPION else b"nexus-trial-version-v1\0"
            )
            expected_hash = hashlib.sha256(domain + encoded.encode("utf-8")).hexdigest()
            if version["version_hash"] != expected_hash:
                raise NexusTradeSingletonError("candidate version hash is invalid")
        version_data = dict(version)
        version_data["snapshot"] = snapshot
        return version_data

    async def get_runtime_snapshot(self) -> dict:
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN")
            try:
                snapshot = await self._snapshot_from_connection(db)
            except BaseException:
                await db.rollback()
                raise
            await db.commit()
            return snapshot

    async def set_champion_mode(
        self, *, enabled: bool, account_id: str, account_type: str,
    ) -> dict:
        if type(enabled) is not bool:
            raise TypeError("enabled must be boolean")
        normalized_account_id = str(account_id or "").strip()
        normalized_account_type = str(account_type or "").lower()
        if normalized_account_type not in {"demo", "real"}:
            raise ValueError("Champion account type must be demo or real")
        if enabled and not normalized_account_id:
            raise ValueError("Champion ON requires a selected account")
        if not enabled and normalized_account_type != "demo":
            raise ValueError("Champion OFF must remain on DEMO")
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    UPDATE nexus_runtime
                    SET champion_enabled = ?, champion_account_id = ?,
                        champion_account_type = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE bot_id = ?
                    """,
                    (
                        int(enabled),
                        normalized_account_id,
                        normalized_account_type,
                        NEXUS_TRADE_BOT_ID,
                    ),
                )
                await self._advance_snapshot_version(db)
                snapshot = await self._snapshot_from_connection(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    async def set_emergency_stop(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise TypeError("emergency_stop must be boolean")
        async with self._connection() as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    UPDATE nexus_runtime
                    SET emergency_stop = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE bot_id = ?
                    """,
                    (int(enabled), NEXUS_TRADE_BOT_ID),
                )
                await self._advance_snapshot_version(db)
                snapshot = await self._snapshot_from_connection(db)
                await db.commit()
                return snapshot
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    async def _advance_snapshot_version(db: aiosqlite.Connection) -> None:
        cursor = await db.execute(
            """
            UPDATE bot_instances
            SET config_revision = config_revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND strategy_id = 'nexus_trade'
            """,
            (NEXUS_TRADE_BOT_ID,),
        )
        if cursor.rowcount != 1:
            raise NexusTradeSingletonError("NexusTrade singleton is unavailable")

    async def get_control_snapshot(self) -> dict:
        durable = await self.get_runtime_snapshot()
        decisions = await self._list_json_rows(
            """
            SELECT * FROM nexus_decisions
            ORDER BY created_at DESC, id DESC LIMIT 100
            """,
            json_fields=("payload",),
        )
        trades = await self._list_json_rows(
            """
            SELECT * FROM trades WHERE bot_id = ?
            ORDER BY id DESC LIMIT 100
            """,
            params=(NEXUS_TRADE_BOT_ID,),
            json_fields=("metadata",),
        )
        runtime = durable["runtime"]
        return {
            "schema_version": 1,
            "snapshot_version": int(durable["bot"].get("config_revision") or 1),
            "bot_id": NEXUS_TRADE_BOT_ID,
            "runtime": runtime,
            "emergency_stop": bool(runtime.get("emergency_stop", 0)),
            "lanes": durable["lanes"],
            "active_campaigns": durable["active_campaigns"],
            "decisions": decisions,
            "trades": trades,
            "reports": await self.list_reports(),
            "proposals": await self.list_proposals(),
        }

    async def list_versions(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_versions ORDER BY created_at, id",
            json_fields=("snapshot",),
        )

    async def list_campaigns(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_campaigns ORDER BY started_at, id",
        )

    async def list_reports(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_reports ORDER BY created_at DESC, id DESC",
            json_fields=("snapshot",),
        )

    async def list_proposals(self) -> list:
        return await self._list_json_rows(
            "SELECT * FROM nexus_proposals ORDER BY created_at DESC, id DESC",
            json_fields=("payload",),
        )

    async def _list_json_rows(
        self, query: str, *, params=(), json_fields=(),
    ) -> list:
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
        for row in rows:
            for field in json_fields:
                raw_value = row.get(field)
                if raw_value is not None:
                    row[field] = json.loads(raw_value)
        return rows
