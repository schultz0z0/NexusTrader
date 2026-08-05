from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database.repository import DatabaseRepository

router = APIRouter(prefix="/api/v1/config", tags=["RiskConfig"])
db = DatabaseRepository()

class RiskConfigSchema(BaseModel):
    initial_stake: float = 1.0
    stop_loss_daily: float = 50.0
    take_profit_daily: float = 100.0
    max_daily_trades: int = 50
    max_single_stake: float = 20.0
    max_consecutive_losses: int = 3
    cooldown_minutes: int = 15

@router.get("/risk")
async def get_risk_config():
    config = await db.get_risk_config()
    return {"status": "success", "data": config}

@router.post("/risk")
async def update_risk_config(payload: RiskConfigSchema):
    await db.update_risk_config(payload.model_dump())
    return {"status": "success", "message": "Configuração de risco atualizada dinamicamente!", "data": payload.model_dump()}
