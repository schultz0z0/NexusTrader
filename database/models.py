from datetime import datetime
from typing import Optional
import json

class DatabaseModels:
    """
    Modelos de tabelas para persistencia de dados do NexusTrader.
    """
    
    @staticmethod
    def create_tables_sql() -> str:
        return """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS risk_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initial_stake REAL DEFAULT 1.0,
            stop_loss_daily REAL DEFAULT 50.0,
            take_profit_daily REAL DEFAULT 100.0,
            max_daily_trades INTEGER DEFAULT 50,
            max_single_stake REAL DEFAULT 20.0,
            max_consecutive_losses INTEGER DEFAULT 3,
            cooldown_minutes INTEGER DEFAULT 15,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT,
            session_id TEXT,
            strategy_name TEXT,
            symbol TEXT,
            contract_type TEXT,
            contract_id INTEGER,
            stake REAL,
            payout REAL,
            profit REAL,
            result TEXT,
            status TEXT DEFAULT 'closed',
            entry_spot REAL,
            exit_spot REAL,
            purchase_time INTEGER,
            expiry_time INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            account_type TEXT DEFAULT 'demo',
            symbol TEXT DEFAULT 'R_100',
            strategy TEXT DEFAULT 'BollingerBands(20, 2.0)',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bot_instances (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
strategy_id TEXT NOT NULL DEFAULT 'donchian',
            strategy_config TEXT NOT NULL DEFAULT '{}',
            account_id TEXT NOT NULL,
            account_type TEXT NOT NULL DEFAULT 'demo',
symbol TEXT NOT NULL DEFAULT 'R_75',
            timeframe_seconds INTEGER NOT NULL DEFAULT 60,
duration INTEGER NOT NULL DEFAULT 2,
duration_unit TEXT NOT NULL DEFAULT 'm',
            initial_stake REAL NOT NULL DEFAULT 1.0,
            money_management TEXT NOT NULL DEFAULT 'fixed',
            money_config TEXT NOT NULL DEFAULT '{}',
            risk_config TEXT NOT NULL DEFAULT '{}',
            desired_state TEXT NOT NULL DEFAULT 'STOPPED',
            runtime_state TEXT NOT NULL DEFAULT 'STOPPED',
            heartbeat_at TIMESTAMP,
            last_error TEXT,
            config_revision INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_intents (
            id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            session_id TEXT,
            proposal_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            contract_type TEXT NOT NULL,
            stake REAL NOT NULL,
            price REAL NOT NULL,
            duration INTEGER NOT NULL,
            duration_unit TEXT NOT NULL,
            signal_epoch INTEGER,
            state TEXT NOT NULL DEFAULT 'prepared',
            contract_id INTEGER,
            transaction_id INTEGER,
            error TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY(bot_id) REFERENCES bot_instances(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_order_intents_account_unresolved
        ON order_intents(account_id)
        WHERE state IN ('prepared', 'submitting', 'reconcile_pending', 'ambiguous');

        CREATE UNIQUE INDEX IF NOT EXISTS ux_order_intents_contract
        ON order_intents(contract_id)
        WHERE contract_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS risk_states (
            bot_id TEXT PRIMARY KEY,
            current_stake REAL NOT NULL,
            current_level INTEGER NOT NULL DEFAULT 0,
            consecutive_wins INTEGER NOT NULL DEFAULT 0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            circuit_consecutive_losses INTEGER NOT NULL DEFAULT 0,
            circuit_tripped_at REAL NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(bot_id) REFERENCES bot_instances(id)
        );

        CREATE TABLE IF NOT EXISTS runtime_health (
            bot_id TEXT PRIMARY KEY,
            deriv_connected INTEGER NOT NULL DEFAULT 0,
            publisher_healthy INTEGER NOT NULL DEFAULT 0,
            market_epoch INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(bot_id) REFERENCES bot_instances(id)
        );

        CREATE TABLE IF NOT EXISTS service_heartbeats (
            service_name TEXT PRIMARY KEY,
            details TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        """
