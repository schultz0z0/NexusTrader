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
            session_id TEXT,
            strategy_name TEXT,
            symbol TEXT,
            contract_type TEXT,
            contract_id INTEGER,
            stake REAL,
            payout REAL,
            profit REAL,
            result TEXT,
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
        """
