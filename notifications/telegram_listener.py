import asyncio
import httpx
from typing import Optional
from config.settings import settings
from database.repository import DatabaseRepository
from api.routes.bot_control import BOT_STATE
from utils.logger import setup_logger

logger = setup_logger("TelegramListener")

class TelegramCommandListener:
    """
    Escuta e processa comandos enviados pelo usuario via Telegram em tempo real.
    Permite controlar o robo (/iniciar, /pausar, /status, /stake, /ativo, etc).
    """
    def __init__(self, bot_token: str = None, allowed_chat_id: str = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.allowed_chat_id = str(allowed_chat_id or settings.TELEGRAM_CHAT_ID)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.db = DatabaseRepository()
        self._client = httpx.AsyncClient(timeout=20.0)
        self._offset = 0
        self._is_running = False

    async def send_reply(self, chat_id: str, text: str):
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            await self._client.post(url, json=payload)
        except Exception as e:
            logger.error(f"Erro ao enviar resposta no Telegram: {e}")

    async def process_command(self, chat_id: str, text: str):
        # Trava de seguranca: apenas o dono registrado pode controlar
        if self.allowed_chat_id and str(chat_id) != self.allowed_chat_id:
            logger.warning(f"Comando ignorado de Chat ID nao autorizado: {chat_id}")
            return

        parts = text.strip().split()
        if not parts:
            return

        cmd = parts[0].lower().replace("@", " ").split()[0]  # Trata usernames do bot se houver

        if cmd in ["/start", "/iniciar"]:
            BOT_STATE["status"] = "RUNNING"
            msg = (
                "🟢 <b>Robô INICIADO via Telegram!</b>\n\n"
                "As operações estão ativas. Use /status para visualizar o desempenho em tempo real."
            )
            await self.send_reply(chat_id, msg)

        elif cmd in ["/stop", "/pausar"]:
            BOT_STATE["status"] = "STOPPED"
            msg = (
                "🔴 <b>Robô PAUSADO via Telegram!</b>\n\n"
                "Nenhum novo contrato será aberto. Use /iniciar para retomar."
            )
            await self.send_reply(chat_id, msg)

        elif cmd in ["/status", "/relatorio"]:
            stats = await self.db.get_daily_stats()
            bot_settings = await self.db.get_bot_settings()
            risk_cfg = await self.db.get_risk_config()

            pnl = stats.get("daily_pnl", 0.0)
            trades = stats.get("daily_trades", 0)
            status_emoji = "🟢 OPERANDO" if BOT_STATE.get("status") == "RUNNING" else "🔴 PAUSADO"

            msg = (
                f"📊 <b>Painel de Status NexusTrader</b>\n\n"
                f"Status: <b>{status_emoji}</b>\n"
                f"Conta: <code>{bot_settings.get('account_id', 'N/A')}</code>\n"
                f"Ativo: <b>{bot_settings.get('symbol', 'R_100')}</b>\n"
                f"Stake Inicial: <b>${risk_cfg.get('initial_stake', 1.0):.2f} USD</b>\n\n"
                f"📈 <b>PnL Hoje:</b> <b>{'+' if pnl >= 0 else ''}${pnl:.2f} USD</b>\n"
                f"🔢 <b>Total de Trades:</b> {trades}\n"
                f"🛑 <b>Stop Loss Diário:</b> ${risk_cfg.get('stop_loss_daily', 50.0):.2f}\n"
                f"🏆 <b>Take Profit Diário:</b> ${risk_cfg.get('take_profit_daily', 100.0):.2f}"
            )
            await self.send_reply(chat_id, msg)

        elif cmd == "/stake":
            if len(parts) > 1:
                try:
                    new_val = float(parts[1])
                    risk_cfg = await self.db.get_risk_config()
                    risk_cfg["initial_stake"] = new_val
                    await self.db.update_risk_config(risk_cfg)
                    await self.send_reply(chat_id, f"✅ <b>Stake Inicial alterada para ${new_val:.2f} USD!</b>\nSincronizado com o site.")
                except ValueError:
                    await self.send_reply(chat_id, "❌ Valor inválido. Exemplo correto: <code>/stake 5.0</code>")
            else:
                await self.send_reply(chat_id, "ℹ️ Informe o valor da Stake. Exemplo: <code>/stake 2.5</code>")

        elif cmd in ["/stoploss", "/sl"]:
            if len(parts) > 1:
                try:
                    new_val = float(parts[1])
                    risk_cfg = await self.db.get_risk_config()
                    risk_cfg["stop_loss_daily"] = new_val
                    await self.db.update_risk_config(risk_cfg)
                    await self.send_reply(chat_id, f"🛑 <b>Daily Stop Loss alterado para ${new_val:.2f} USD!</b>")
                except ValueError:
                    await self.send_reply(chat_id, "❌ Valor inválido. Exemplo correto: <code>/stoploss 100</code>")
            else:
                await self.send_reply(chat_id, "ℹ️ Informe o valor do Stop Loss. Exemplo: <code>/stoploss 50</code>")

        elif cmd in ["/takeprofit", "/tp"]:
            if len(parts) > 1:
                try:
                    new_val = float(parts[1])
                    risk_cfg = await self.db.get_risk_config()
                    risk_cfg["take_profit_daily"] = new_val
                    await self.db.update_risk_config(risk_cfg)
                    await self.send_reply(chat_id, f"🏆 <b>Daily Take Profit alterado para ${new_val:.2f} USD!</b>")
                except ValueError:
                    await self.send_reply(chat_id, "❌ Valor inválido. Exemplo correto: <code>/takeprofit 200</code>")
            else:
                await self.send_reply(chat_id, "ℹ️ Informe a Meta de Lucro. Exemplo: <code>/takeprofit 100</code>")

        elif cmd == "/ativo":
            assets_info = (
                "📊 <b>Ativos Disponíveis para Operar:</b>\n\n"
                "🔹 <b>Índices Padrões (Recomendados):</b>\n"
                "• <code>/ativo R_10</code> - Volatility 10 Index\n"
                "• <code>/ativo R_25</code> - Volatility 25 Index\n"
                "• <code>/ativo R_50</code> - Volatility 50 Index\n"
                "• <code>/ativo R_75</code> - Volatility 75 Index\n"
                "• <code>/ativo R_100</code> - Volatility 100 Index\n\n"
                "⚡ <b>Índices de 1 Segundo (Alta Velocidade):</b>\n"
                "• <code>/ativo 1HZ10V</code> - Volatility 10 (1s) Index\n"
                "• <code>/ativo 1HZ25V</code> - Volatility 25 (1s) Index\n"
                "• <code>/ativo 1HZ50V</code> - Volatility 50 (1s) Index\n"
                "• <code>/ativo 1HZ75V</code> - Volatility 75 (1s) Index\n"
                "• <code>/ativo 1HZ100V</code> - Volatility 100 (1s) Index"
            )

            if len(parts) > 1:
                symbol = parts[1].upper()
                valid_symbols = ["R_10", "R_25", "R_50", "R_75", "R_100", "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]
                if symbol in valid_symbols:
                    bot_settings = await self.db.get_bot_settings()
                    bot_settings["symbol"] = symbol
                    await self.db.update_bot_settings(bot_settings)
                    await self.send_reply(chat_id, f"🔄 <b>Ativo alterado com sucesso para {symbol}!</b>\nO robô está reconectando a sessão...")
                else:
                    await self.send_reply(chat_id, f"❌ Código de ativo <b>'{symbol}'</b> inválido.\n\n{assets_info}")
            else:
                await self.send_reply(chat_id, assets_info)

        elif cmd in ["/ajuda", "/menu", "/help"]:
            msg = (
                "🤖 <b>Menu de Comandos NexusTrader</b>\n\n"
                "🟢 /iniciar - Ativa as operações\n"
                "🔴 /pausar - Pausa o robô\n"
                "📊 /status - Exibe o saldo, PnL e métricas de hoje\n"
                "💵 /stake [valor] - Altera a entrada inicial (ex: <code>/stake 5</code>)\n"
                "🛑 /stoploss [valor] - Altera o limite de perda (ex: <code>/stoploss 50</code>)\n"
                "🏆 /takeprofit [valor] - Altera a meta de lucro (ex: <code>/takeprofit 100</code>)\n"
                "📈 /ativo - Lista os mercados disponíveis e altera (ex: <code>/ativo R_75</code>)\n"
                "❓ /ajuda - Exibe este menu de ajuda"
            )
            await self.send_reply(chat_id, msg)

    async def start_polling(self):
        if not self.bot_token:
            logger.warning("Telegram Bot Token ausente. Listener de comandos nao iniciado.")
            return

        self._is_running = True
        logger.info("⚡ TelegramCommandListener iniciado (escutando comandos do usuario em tempo real)...")

        while self._is_running:
            try:
                url = f"{self.base_url}/getUpdates?offset={self._offset}&timeout=10"
                res = await self._client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    for update in data.get("result", []):
                        self._offset = update.get("update_id", 0) + 1
                        message = update.get("message", {})
                        text = message.get("text", "")
                        chat_id = str(message.get("chat", {}).get("id", ""))

                        if text.startswith("/"):
                            await self.process_command(chat_id, text)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erro no polling de comandos do Telegram: {e}")
                await asyncio.sleep(5)

    def stop_polling(self):
        self._is_running = False

    async def close(self):
        self.stop_polling()
        await self._client.aclose()
