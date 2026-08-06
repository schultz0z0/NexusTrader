import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Credenciais da Deriv (Novo Sistema OAuth + PAT)
    DERIV_APP_ID: str
    DERIV_API_TOKEN: str
    DERIV_ACCOUNT_ID: str = ""
    
    # Endpoints
    DERIV_REST_BASE_URL: str = "https://api.derivws.com"
    # Configuracoes
    DERIV_ACCOUNT_TYPE: str = "demo"
    LOG_LEVEL: str = "INFO"
    DB_URL: str = "sqlite+aiosqlite:///nexus_trader.db"
    DB_PATH: str = "nexus_trader.db"
    DOMAIN: str = ""
    DEV_MODE: bool = False

    # Runtime e seguranca entre os containers
    INTERNAL_API_TOKEN: str = ""
    DASHBOARD_API_KEY: str = ""
    API_BASE_URL: str = "http://127.0.0.1:8000"
    BUSINESS_TIMEZONE: str = "America/Sao_Paulo"
    ALLOW_REAL_TRADING: bool = False
    EVENT_QUEUE_MAX: int = 2000
    RUNTIME_HEARTBEAT_SECONDS: int = 5
    MARKET_STALE_AFTER_SECONDS: int = 15
    SETTLEMENT_WAIT_TIMEOUT_SECONDS: int = 600
    MARKET_HISTORY_RESYNC_SECONDS: int = 30
    
    # Notificacoes
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    DISCORD_WEBHOOK_URL: str = ""

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if not self.DEV_MODE and not self.INTERNAL_API_TOKEN.strip():
            raise ValueError("INTERNAL_API_TOKEN e obrigatorio fora do DEV_MODE")
        if not self.DEV_MODE and not self.DASHBOARD_API_KEY.strip():
            raise ValueError("DASHBOARD_API_KEY e obrigatorio fora do DEV_MODE")
        if self.EVENT_QUEUE_MAX < 100:
            raise ValueError("EVENT_QUEUE_MAX deve ser pelo menos 100")
        if self.SETTLEMENT_WAIT_TIMEOUT_SECONDS < 1:
            raise ValueError("SETTLEMENT_WAIT_TIMEOUT_SECONDS deve ser positivo")
        if self.MARKET_HISTORY_RESYNC_SECONDS < 5:
            raise ValueError("MARKET_HISTORY_RESYNC_SECONDS deve ser pelo menos 5")
        return self

settings = Settings()
