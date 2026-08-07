import secrets

from fastapi import Header, HTTPException, Query

from config.settings import settings


async def require_dashboard_key(x_api_key: str = Header(default="")):
    expected = settings.DASHBOARD_API_KEY.strip()
    if not expected and not settings.DEV_MODE:
        raise HTTPException(status_code=503, detail="Dashboard sem credencial configurada")
    if expected and not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Chave do dashboard invalida")


def validate_websocket_key(key: str = ""):
    expected = settings.DASHBOARD_API_KEY.strip()
    if not expected and not settings.DEV_MODE:
        return False
    if expected and not secrets.compare_digest(key, expected):
        return False
    return True


async def require_internal_token(x_internal_token: str = Header(default="")):
    expected = settings.INTERNAL_API_TOKEN.strip()
    if not expected and not settings.DEV_MODE:
        raise HTTPException(status_code=503, detail="Canal interno sem credencial configurada")
    if expected and not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="Token interno invalido")

