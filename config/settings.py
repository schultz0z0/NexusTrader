import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Credenciais da Deriv (Novo Sistema OAuth + PAT)
    DERIV_APP_ID: str
    DERIV_API_TOKEN: str
    DERIV_ACCOUNT_ID: str = ""
    
    # Endpoints
    DERIV_REST_BASE_URL: str = "https://api.derivws.com"
    DERIV_WS_ENDPOINT: str = "wss://ws.derivws.com/websockets/v3"
    
    # Configuracoes
    DERIV_ACCOUNT_TYPE: str = "demo"
    LOG_LEVEL: str = "INFO"
    DB_URL: str = "sqlite+aiosqlite:///nexus_trader.db"
    DB_PATH: str = "nexus_trader.db"
    DOMAIN: str = ""
    
    # Notificacoes
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    DISCORD_WEBHOOK_URL: str = ""

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
