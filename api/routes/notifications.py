from fastapi import APIRouter
from notifications.manager import NotificationManager

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])
notifier = NotificationManager()

@router.post("/test-telegram")
async def test_telegram_notification():
    success = await notifier.telegram.send_message(
        "🧪 <b>NexusTrader - Teste de Notificação</b>\n\n"
        "Seu robô está conectado com sucesso ao Telegram! Você receberá atualizações de operações e alertas de risco por aqui."
    )
    if success:
        return {"status": "success", "message": "Notificação enviada ao Telegram!"}
    else:
        return {"status": "error", "message": "Falha ao enviar notificação ao Telegram. Verifique os logs."}

@router.get("/status")
async def get_notification_status():
    return {
        "status": "success",
        "data": {
            "telegram_configured": notifier.telegram.is_configured,
            "telegram_chat_id": notifier.telegram.chat_id,
            "discord_configured": notifier.discord.is_configured
        }
    }
