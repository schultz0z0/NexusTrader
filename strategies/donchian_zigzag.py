from typing import Dict, Any, Optional, List
import pandas as pd

from strategies.base import BaseStrategy, Signal, MoneyManager
from utils.logger import setup_logger

logger = setup_logger("DonchianStrategy")

class DonchianZigZagStrategy(BaseStrategy):
    """
    Estrategia que utiliza Donchian Channel (21) e ZigZag (1, 15, 3).
    Abre operacao de 2 minutos apostando em reversao quando o preco 
    toca as extremidades do Donchian Channel, validado pelo ZigZag.
    """
    def __init__(self, money_manager: Optional[MoneyManager] = None):
        super().__init__(money_manager or MoneyManager())
        self.period = 21
        self.zigzag_dev = 1.0 # 1%
        # Parametros fixos exigidos pela estrategia
        self.duration = 2
        self.duration_unit = 'm'
        
        logger.info("Estrategia Donchian ZigZag inicializada.")

    def _calculate_donchian(self, df: pd.DataFrame) -> pd.DataFrame:
        df['donchian_upper'] = df['high'].rolling(window=self.period).max()
        df['donchian_lower'] = df['low'].rolling(window=self.period).min()
        return df
        
    def analyze(self, ticks: List[Dict[str, Any]], candles: Optional[List[Dict[str, Any]]] = None) -> Optional[Signal]:
        if len(ticks) < self.period:
            return None
            
        # O MarketDataHandler envia `ticks` contendo apenas 'quote' e 'epoch'.
        # Como o robô roda em timeframe de 1m, criamos o dataframe com os precos 
        # (close simulando o tick) para a matematica do Donchian funcionar.
        quotes = [t['quote'] for t in ticks]
        df = pd.DataFrame({'high': quotes, 'low': quotes, 'close': quotes})
        
        # Calcula Donchian
        df = self._calculate_donchian(df)
        
        current_candle = df.iloc[-1]
        
        # Upper e Lower atuais do Donchian
        d_upper = current_candle['donchian_upper']
        d_lower = current_candle['donchian_lower']
        
        # Preco atual e tempo
        current_price = quotes[-1]
        last_time = ticks[-1].get('epoch', 0)
        
        if last_time <= self._last_signal_time:
            return None
        
        # O ZigZag em tempo real (Depth 15) garante que o Donchian (21) é um extremo absoluto.
        # Adicionamos a validação do Desvio (Deviation = 1%)
        
        is_touching_upper = current_price >= d_upper
        is_touching_lower = current_price <= d_lower
        
        # Filtro de Deviation (1%) do ZigZag
        min_recente = df['low'].tail(15).min()
        max_recente = df['high'].tail(15).max()
        
        valido_para_put = is_touching_upper and (current_price >= min_recente * (1 + self.zigzag_dev/100))
        valido_para_call = is_touching_lower and (current_price <= max_recente * (1 - self.zigzag_dev/100))
        
        if valido_para_put:
            logger.info(f"Preço {current_price} tocou Donchian Upper e validou ZigZag 1%. Sinal PUT.")
            self._last_signal_time = last_time
            return Signal(action="PUT", reason=f"Tocou Donchian Sup. e variou 1% da minima", price=current_price, timestamp=last_time)
            
        if valido_para_call:
            logger.info(f"Preço {current_price} tocou Donchian Lower e validou ZigZag 1%. Sinal CALL.")
            self._last_signal_time = last_time
            return Signal(action="CALL", reason=f"Tocou Donchian Inf. e variou 1% da maxima", price=current_price, timestamp=last_time)

        return None
