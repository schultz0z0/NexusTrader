class NexusModels:
    """SQLite schema owned exclusively by NexusTrade."""

    @staticmethod
    def create_tables_sql() -> str:
        return """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_bot_instances_nexus_trade_singleton
        ON bot_instances(strategy_id) WHERE strategy_id = 'nexus_trade';

        CREATE TABLE IF NOT EXISTS nexus_versions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            version_hash TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_runtime (
            bot_id TEXT PRIMARY KEY,
            champion_version_id TEXT NOT NULL,
            trial_version_id TEXT,
            champion_enabled INTEGER NOT NULL DEFAULT 0,
            champion_account_id TEXT NOT NULL DEFAULT '',
            champion_account_type TEXT NOT NULL DEFAULT 'demo',
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(bot_id) REFERENCES bot_instances(id),
            FOREIGN KEY(champion_version_id) REFERENCES nexus_versions(id),
            FOREIGN KEY(trial_version_id) REFERENCES nexus_versions(id)
        );

        CREATE TABLE IF NOT EXISTS nexus_campaigns (
            id TEXT PRIMARY KEY,
            lane TEXT NOT NULL,
            nexus_version_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            FOREIGN KEY(nexus_version_id) REFERENCES nexus_versions(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_campaigns_active_trial
        ON nexus_campaigns(lane) WHERE lane = 'challenger_trial' AND status = 'ACTIVE';

        CREATE TABLE IF NOT EXISTS nexus_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            open_epoch INTEGER NOT NULL,
            close_epoch INTEGER,
            open REAL, high REAL, low REAL, close REAL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_candles_symbol_open_epoch
        ON nexus_candles(symbol, open_epoch);

        CREATE TABLE IF NOT EXISTS nexus_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            open_epoch INTEGER NOT NULL,
            nexus_version_id TEXT,
            values_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_features_symbol_open_epoch_version
        ON nexus_features(symbol, open_epoch, nexus_version_id);

        CREATE TABLE IF NOT EXISTS nexus_decisions (
            id TEXT PRIMARY KEY,
            lane TEXT NOT NULL,
            nexus_version_id TEXT NOT NULL,
            campaign_id TEXT,
            symbol TEXT NOT NULL,
            signal_epoch INTEGER NOT NULL,
            entry_delay_ms INTEGER,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_candidates (
            id TEXT PRIMARY KEY,
            nexus_version_id TEXT,
            artifact_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_reports (
            id TEXT PRIMARY KEY,
            campaign_id TEXT,
            report_hash TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_proposals (
            id TEXT PRIMARY KEY,
            campaign_id TEXT,
            nexus_version_id TEXT,
            revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_audit_events (
            id TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_tick_segments (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            start_epoch INTEGER NOT NULL,
            end_epoch INTEGER NOT NULL,
            tick_count INTEGER NOT NULL,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
