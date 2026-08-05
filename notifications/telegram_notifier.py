import httpx
from typing import Optional, Dict, Any
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("TelegramNotifier")

class TelegramNotifier:
    """
    Servico assincrono para envio de notificacoes via Telegram Bot API.
    Usa a biblioteca httpx (assincrona, leve e sem dependencias pesadas).
    """
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def auto_discover_chat_id(self) -> Optional[str]:
        """
        Caso o chat_id esteja vazio, busca a ultima conversa com o Bot via getUpdates.
        """
        if not self.bot_token:
            return None
            
        try:
            url = f"{self.base_url}/getUpdates"
            res = await self._client.get(url)
            if res.status_code == 200:
                data = res.json()
                results = data.get("result", [])
                if results:
                    # Pega o id do remetente da mensagem mais recente
                    last_msg = results[-1].get("message", {})
                    chat = last_msg.get("chat", {})
                    cid = str(chat.get("id"))
                    if cid:
                        self.chat_id = cid
                        logger.info(f"Chat ID autodetectado com sucesso no Telegram: {cid}")
                        return cid
        except Exception as e:
            logger.error(f"Erro ao autodetectar Chat ID no Telegram: {e}")
        return None

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envia uma mensagem de texto simples ou HTML para o chat do Telegram."""
        if not self.bot_token:
            logger.warning("Telegram Bot Token nao configurado.")
            return False
            
        if not self.chat_id:
            logger.info("Chat ID nao configurado. Tentando autodetectar via getUpdates...")
            await self.auto_discover_chat_id()
            
        if not self.chat_id:
            logger.error("Nao foi possivel enviar notificacao: Chat ID do Telegram ausente.")
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode
            }
            response = await self._client.post(url, json=payload)
            if response.status_code == 200:
                logger.info("Notificacao enviada ao Telegram com sucesso!")
                return True
            else:
                logger.error(f"Falha ao enviar mensagem ao Telegram: HTTP {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Erro na requisicao ao Telegram: {e}")
            return False

    async def send_session_start(self, account_id: str, symbol: str, balance: float):
        """Notifica inicio ou reinicio de sessao do robo."""
        type_str = "Demo" if "DOT" in account_id or "ROT" not in account_id else "Real"
        text = (
            f"🚀 <b>NexusTrader Iniciado!</b>\n\n"
            f"👤 <b>Conta:</b> {account_id} ({type_str})\n"
            f"📊 <b>Ativo:</b> {symbol}\n"
            f"💰 <b>Saldo Inicial:</b> ${balance:.2f} USD\n"
            f"⚡ <i>Monitorando mercado em tempo real...</i>"
        )
        await self.send_message(text)

    async def send_trade_result(self, trade_data: Dict[str, Any], daily_pnl: float = 0.0):
        """Notifica fechamento de trade (Win ou Loss)."""
        is_win = trade_data.get('result') == 'won' or trade_data.get('profit', 0) > 0
        emoji = "🟢 <b>WIN!</b>" if is_win else "🔴 <b>LOSS!</b>"
        profit = trade_data.get('profit', 0.0)
        profit_str = f"+${profit:.2f}" if profit >= 0 else f"-${abs(profit):.2f}"
        
        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"

        text = (
            f"{emoji}\n\n"
            f"🎯 <b>Contrato:</b> #{trade_data.get('contract_id', 'N/A')}\n"
            f"📊 <b>Ativo:</b> {trade_data.get('symbol', 'N/A')} | <b>Tipo:</b> {trade_data.get('contract_type')}\n"
            f"💵 <b>Stake:</b> ${trade_data.get('stake', 0.0):.2f}\n"
            f"💰 <b>Lucro/Prejuízo:</b> <b>{profit_str}</b>\n\n"
            f"{pnl_emoji} <b>PnL Acumulado Hoje:</b> <b>{pnl_str} USD</b>"
        )
        await self.send_message(text)

    async def send_risk_alert(self, title: str, description: str, alert_type: str = "WARNING"):
        """Notifica alertas de risco (Stop Loss, Take Profit, Circuit Breaker)."""
        icon = "⚠️"
        if alert_type == "STOP_LOSS":
            icon = "🛑"
        elif alert_type == "TAKE_PROFIT":
            icon = "🏆"
        elif alert_type == "CIRCUIT_BREAKER":
            icon = "⚡"

        text = (
            f"{icon} <b>{title.upper()}</b> {icon}\n\n"
            f"{description}"
        )
        await self.send_message(text)

    async def close(self):
        await self._client.aclose()
