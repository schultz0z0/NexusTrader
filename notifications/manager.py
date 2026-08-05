import asyncio
from typing import Dict, Any, Optional
from notifications.telegram_notifier import TelegramNotifier
from notifications.telegram_listener import TelegramCommandListener
from notifications.discord_notifier import DiscordNotifier
from utils.logger import setup_logger

logger = setup_logger("NotificationManager")

class NotificationManager:
    """
    Gerenciador Central de Notificacoes.
    Despacha mensagens para Telegram e/ou Discord de forma assincrona e nao-bloqueante.
    """
    def __init__(self):
        self.telegram = TelegramNotifier()
        self.telegram_listener = TelegramCommandListener()
        self.discord = DiscordNotifier()
        self._listener_task = None

    def start_command_listener(self):
        """Inicia a escuta de comandos do Telegram em segundo plano."""
        if not self._listener_task and self.telegram_listener.bot_token:
            self._listener_task = asyncio.create_task(self.telegram_listener.start_polling())

    def notify_session_start(self, account_id: str, symbol: str, balance: float):
        """Despacha mensagem de inicio de sessao sem bloquear o chamador."""
        asyncio.create_task(self._safe_notify_session_start(account_id, symbol, balance))

    async def _safe_notify_session_start(self, account_id: str, symbol: str, balance: float):
        try:
            if self.telegram.is_configured or self.telegram.bot_token:
                await self.telegram.send_session_start(account_id, symbol, balance)
            if self.discord.is_configured:
                await self.discord.send_embed(
                    title="🚀 NexusTrader Iniciado",
                    description=f"Sessao ativa na conta `{account_id}` para o ativo `{symbol}`. Saldo: ${balance:.2f} USD.",
                    color=0x00FF00
                )
        except Exception as e:
            logger.error(f"Erro ao despachar notificacao de inicio de sessao: {e}")

    def notify_trade_result(self, trade_data: Dict[str, Any], daily_pnl: float = 0.0):
        """Despacha resultado de trade sem bloquear o chamador."""
        asyncio.create_task(self._safe_notify_trade_result(trade_data, daily_pnl))

    async def _safe_notify_trade_result(self, trade_data: Dict[str, Any], daily_pnl: float = 0.0):
        try:
            if self.telegram.is_configured or self.telegram.bot_token:
                await self.telegram.send_trade_result(trade_data, daily_pnl)
            if self.discord.is_configured:
                is_win = trade_data.get('result') == 'won' or trade_data.get('profit', 0) > 0
                color = 0x00FF00 if is_win else 0xFF0000
                await self.discord.send_embed(
                    title=f"{'🟢 WIN' if is_win else '🔴 LOSS'} em {trade_data.get('symbol')}",
                    description=f"Contrato #{trade_data.get('contract_id')} ({trade_data.get('contract_type')})\nLucro: ${trade_data.get('profit', 0):.2f} USD",
                    color=color
                )
        except Exception as e:
            logger.error(f"Erro ao despachar notificacao de trade: {e}")

    def notify_risk_alert(self, title: str, description: str, alert_type: str = "WARNING"):
        """Despacha alerta de risco sem bloquear o chamador."""
        asyncio.create_task(self._safe_notify_risk_alert(title, description, alert_type))

    async def _safe_notify_risk_alert(self, title: str, description: str, alert_type: str = "WARNING"):
        try:
            if self.telegram.is_configured or self.telegram.bot_token:
                await self.telegram.send_risk_alert(title, description, alert_type)
            if self.discord.is_configured:
                color = 0xFF9900 if alert_type == "WARNING" else 0xFF0000
                await self.discord.send_embed(
                    title=f"⚠️ {title}",
                    description=description,
                    color=color
                )
        except Exception as e:
            logger.error(f"Erro ao despachar alerta de risco: {e}")

    async def close(self):
        if self._listener_task:
            self._listener_task.cancel()
        await self.telegram_listener.close()
        await self.telegram.close()
        await self.discord.close()
