import httpx
from typing import Dict, Any, Optional
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("DiscordNotifier")

class DiscordNotifier:
    """
    Servico assincrono para envio de Notificacoes e Embeds via Discord Webhook.
    """
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or settings.DISCORD_WEBHOOK_URL
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("http"))

    async def send_embed(self, title: str, description: str, color: int = 0x00FF00, fields: list = None) -> bool:
        if not self.is_configured:
            return False

        try:
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "NexusTrader Automated System"}
            }
            if fields:
                embed["fields"] = fields

            payload = {
                "username": "NexusTrader Bot",
                "embeds": [embed]
            }

            response = await self._client.post(self.webhook_url, json=payload)
            if response.status_code in [200, 204]:
                logger.info("Notificacao enviada ao Discord com sucesso!")
                return True
            else:
                logger.error(f"Falha ao enviar para o Discord: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Erro ao enviar webhook Discord: {e}")
            return False

    async def close(self):
        await self._client.aclose()
