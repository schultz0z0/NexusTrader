class NexusModels:
    """SQLite schema owned exclusively by NexusTrade."""

    @staticmethod
    def create_tables_sql() -> str:
        return """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_bot_instances_nexus_trade_singleton
        ON bot_instances(strategy_id) WHERE strategy_id = 'nexus_trade';

        CREATE TRIGGER IF NOT EXISTS trg_bot_instances_nexus_identity_insert
        BEFORE INSERT ON bot_instances
        WHEN (NEW.id = 'nexus-trade' AND NEW.strategy_id != 'nexus_trade')
          OR (NEW.id != 'nexus-trade' AND NEW.strategy_id = 'nexus_trade')
        BEGIN SELECT RAISE(ABORT, 'nexus-trade identity is reserved'); END;

        CREATE TRIGGER IF NOT EXISTS trg_bot_instances_nexus_identity_update
        BEFORE UPDATE OF id, strategy_id ON bot_instances
        WHEN (NEW.id = 'nexus-trade' AND NEW.strategy_id != 'nexus_trade')
          OR (NEW.id != 'nexus-trade' AND NEW.strategy_id = 'nexus_trade')
        BEGIN SELECT RAISE(ABORT, 'nexus-trade identity is reserved'); END;

        CREATE TABLE IF NOT EXISTS nexus_versions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('CHAMPION', 'TRIAL', 'SHADOW', 'RETIRED')),
            version_hash TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_runtime (
            bot_id TEXT PRIMARY KEY CHECK (bot_id = 'nexus-trade'),
            champion_version_id TEXT NOT NULL,
            trial_version_id TEXT,
            champion_enabled INTEGER NOT NULL DEFAULT 0 CHECK (champion_enabled IN (0, 1)),
            champion_account_id TEXT NOT NULL DEFAULT '',
            champion_account_type TEXT NOT NULL DEFAULT 'demo' CHECK (champion_account_type IN ('demo', 'real')),
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(bot_id) REFERENCES bot_instances(id),
            FOREIGN KEY(champion_version_id) REFERENCES nexus_versions(id),
            FOREIGN KEY(trial_version_id) REFERENCES nexus_versions(id)
        );

        CREATE TABLE IF NOT EXISTS nexus_campaigns (
            id TEXT PRIMARY KEY,
            lane TEXT NOT NULL CHECK (lane IN ('champion_baseline', 'challenger_trial')),
            nexus_version_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'CLOSED')),
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
            lane TEXT NOT NULL CHECK (lane IN ('champion_baseline', 'challenger_trial')),
            nexus_version_id TEXT NOT NULL,
            campaign_id TEXT,
            symbol TEXT NOT NULL,
            signal_epoch INTEGER NOT NULL,
            entry_delay_ms INTEGER CHECK (entry_delay_ms IS NULL OR entry_delay_ms >= 0),
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS nexus_lane_settlements (
            owner_decision_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL CHECK (lane IN ('champion_baseline', 'challenger_trial')),
            contract_id INTEGER NOT NULL CHECK (contract_id > 0),
            settlement_id TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (settlement_id) REFERENCES nexus_decisions(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_lane_settlements_lane_contract
        ON nexus_lane_settlements(lane, contract_id);

        CREATE TABLE IF NOT EXISTS nexus_lane_heads (
            lane TEXT PRIMARY KEY CHECK (lane IN ('champion_baseline', 'challenger_trial')),
            snapshot_id TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (snapshot_id) REFERENCES nexus_decisions(id)
        );

        CREATE TABLE IF NOT EXISTS nexus_repository_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            segment_sequence INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_tick_segments_symbol_sequence
        ON nexus_tick_segments(symbol, segment_sequence);

        CREATE TRIGGER IF NOT EXISTS trg_nexus_versions_values_insert
        BEFORE INSERT ON nexus_versions
        WHEN NEW.status NOT IN ('CHAMPION', 'TRIAL', 'SHADOW', 'RETIRED')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus version status'); END;
        CREATE TRIGGER IF NOT EXISTS trg_nexus_versions_values_update
        BEFORE UPDATE OF status ON nexus_versions
        WHEN NEW.status NOT IN ('CHAMPION', 'TRIAL', 'SHADOW', 'RETIRED')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus version status'); END;

        CREATE TRIGGER IF NOT EXISTS trg_nexus_runtime_values_insert
        BEFORE INSERT ON nexus_runtime
        WHEN NEW.bot_id != 'nexus-trade' OR NEW.champion_enabled NOT IN (0, 1)
          OR NEW.champion_account_type NOT IN ('demo', 'real')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus runtime values'); END;
        CREATE TRIGGER IF NOT EXISTS trg_nexus_runtime_values_update
        BEFORE UPDATE OF bot_id, champion_enabled, champion_account_type ON nexus_runtime
        WHEN NEW.bot_id != 'nexus-trade' OR NEW.champion_enabled NOT IN (0, 1)
          OR NEW.champion_account_type NOT IN ('demo', 'real')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus runtime values'); END;

        CREATE TRIGGER IF NOT EXISTS trg_nexus_campaigns_values_insert
        BEFORE INSERT ON nexus_campaigns
        WHEN NEW.lane NOT IN ('champion_baseline', 'challenger_trial')
          OR NEW.status NOT IN ('ACTIVE', 'SUPERSEDED', 'CLOSED')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus campaign values'); END;
        CREATE TRIGGER IF NOT EXISTS trg_nexus_campaigns_values_update
        BEFORE UPDATE OF lane, status ON nexus_campaigns
        WHEN NEW.lane NOT IN ('champion_baseline', 'challenger_trial')
          OR NEW.status NOT IN ('ACTIVE', 'SUPERSEDED', 'CLOSED')
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus campaign values'); END;

        CREATE TRIGGER IF NOT EXISTS trg_nexus_decisions_values_insert
        BEFORE INSERT ON nexus_decisions
        WHEN NEW.lane NOT IN ('champion_baseline', 'challenger_trial')
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus decision values'); END;
        CREATE TRIGGER IF NOT EXISTS trg_nexus_decisions_values_update
        BEFORE UPDATE OF lane, entry_delay_ms ON nexus_decisions
        WHEN NEW.lane NOT IN ('champion_baseline', 'challenger_trial')
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus decision values'); END;
        """

    @staticmethod
    def create_journal_guards_sql() -> str:
        return """
        CREATE TRIGGER IF NOT EXISTS trg_trades_nexus_values_insert
        BEFORE INSERT ON trades
        WHEN (NEW.lane IS NOT NULL AND NEW.lane NOT IN ('champion_baseline', 'challenger_trial'))
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
          OR (NEW.nexus_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_versions WHERE id = NEW.nexus_version_id))
          OR (NEW.campaign_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_campaigns WHERE id = NEW.campaign_id))
          OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_decisions WHERE id = NEW.decision_id))
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus trade journal values'); END;

        CREATE TRIGGER IF NOT EXISTS trg_trades_nexus_values_update
        BEFORE UPDATE OF lane, nexus_version_id, campaign_id, decision_id, entry_delay_ms ON trades
        WHEN (NEW.lane IS NOT NULL AND NEW.lane NOT IN ('champion_baseline', 'challenger_trial'))
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
          OR (NEW.nexus_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_versions WHERE id = NEW.nexus_version_id))
          OR (NEW.campaign_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_campaigns WHERE id = NEW.campaign_id))
          OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_decisions WHERE id = NEW.decision_id))
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus trade journal values'); END;

        CREATE TRIGGER IF NOT EXISTS trg_order_intents_nexus_values_insert
        BEFORE INSERT ON order_intents
        WHEN (NEW.lane IS NOT NULL AND NEW.lane NOT IN ('champion_baseline', 'challenger_trial'))
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
          OR (NEW.nexus_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_versions WHERE id = NEW.nexus_version_id))
          OR (NEW.campaign_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_campaigns WHERE id = NEW.campaign_id))
          OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_decisions WHERE id = NEW.decision_id))
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus order intent values'); END;

        CREATE TRIGGER IF NOT EXISTS trg_order_intents_nexus_values_update
        BEFORE UPDATE OF lane, nexus_version_id, campaign_id, decision_id, entry_delay_ms ON order_intents
        WHEN (NEW.lane IS NOT NULL AND NEW.lane NOT IN ('champion_baseline', 'challenger_trial'))
          OR (NEW.entry_delay_ms IS NOT NULL AND NEW.entry_delay_ms < 0)
          OR (NEW.nexus_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_versions WHERE id = NEW.nexus_version_id))
          OR (NEW.campaign_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_campaigns WHERE id = NEW.campaign_id))
          OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus_decisions WHERE id = NEW.decision_id))
        BEGIN SELECT RAISE(ABORT, 'invalid Nexus order intent values'); END;
        """
