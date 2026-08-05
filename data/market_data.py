import pandas as pd
from utils.logger import setup_logger
from api.websocket_manager import ws_manager

logger = setup_logger("MarketData")

class MarketDataHandler:
    """
    Gerencia o buffer de dados de mercado em tempo real.
    Calcula os indicadores das Bandas de Bollinger e transmite via WebSocket para a interface Web.
    """
    def __init__(self, connection, buffer_size: int = 500):
        self.connection = connection
        self.buffer_size = buffer_size
        self._ticks = []
        self._subscription_id = None
        
    async def subscribe_ticks(self, symbol: str):
        logger.info(f"Solicitando inscricao de ticks reais para {symbol}...")
        request = {"ticks": symbol}
        
        async def on_tick(data):
            if 'tick' in data:
                tick = data['tick']
                quote = float(tick.get('quote'))
                epoch = tick.get('epoch')
                
                tick_item = {
                    'quote': quote,
                    'epoch': epoch,
                    'symbol': tick.get('symbol')
                }
                self._ticks.append(tick_item)
                
                if len(self._ticks) > self.buffer_size:
                    self._ticks = self._ticks[-self.buffer_size:]
                
                # Calcula Bandas de Bollinger Reais (Periodo 20, Std 2.0)
                upper, lower, sma = None, None, None
                if len(self._ticks) >= 20:
                    quotes = [t['quote'] for t in self._ticks]
                    series = pd.Series(quotes)
                    s_sma = series.rolling(20).mean()
                    s_std = series.rolling(20).std()
                    s_upper = s_sma + (s_std * 2.0)
                    s_lower = s_sma - (s_std * 2.0)
                    
                    import math
                    sma = float(s_sma.iloc[-1])
                    upper = float(s_upper.iloc[-1])
                    lower = float(s_lower.iloc[-1])
                    if math.isnan(sma): sma = None
                    if math.isnan(upper): upper = None
                    if math.isnan(lower): lower = None

                import os
                import httpx
                api_host = os.getenv("API_HOST", "127.0.0.1")
                api_port = os.getenv("API_PORT", "8000")
                tick_url = f"http://{api_host}:{api_port}/api/v1/bot/tick"

                # Transmite o Tick REAL e os Indicadores REAIS para a Interface Web
                tick_payload = {
                    "type": "tick",
                    "symbol": symbol,
                    "price": quote,
                    "epoch": epoch,
                    "upper": upper,
                    "lower": lower,
                    "sma": sma
                }
                
                # Send to FastAPI server
                async def send_to_api():
                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await client.post(tick_url, json=tick_payload, timeout=2.0)
                            if resp.status_code != 200:
                                logger.error(f"Erro ao enviar tick para API. Status: {resp.status_code}")
                    except Exception as e:
                        logger.error(f"Exceção ao enviar tick: {e}")
                
                import asyncio
                asyncio.create_task(send_to_api())

        self._subscription_id = await self.connection.subscribe(request, on_tick)
        logger.info(f"Inscricao de ticks reais para {symbol} ATIVA!")

    def get_latest_tick(self, symbol: str = None):
        if self._ticks:
            return self._ticks[-1]
        return None

    def get_tick_history(self, symbol: str = None):
        return list(self._ticks)
