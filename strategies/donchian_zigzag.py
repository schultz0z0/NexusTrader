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
        self.duration = 2
        self.duration_unit = 'm'
        self._last_signal_time = 0
        
        logger.info("Estrategia Donchian ZigZag inicializada.")

    def name(self) -> str:
        return f"DonchianZigZag({self.period}, {self.zigzag_dev})"

    def get_contract_params(self) -> dict:
        return {"duration": self.duration, "duration_unit": self.duration_unit}

    def _calculate_donchian(self, df: pd.DataFrame) -> pd.DataFrame:
        df['donchian_upper'] = df['high'].rolling(window=self.period).max()
        df['donchian_lower'] = df['low'].rolling(window=self.period).min()
        return df
        
    def analyze(self, ticks: List[Dict[str, Any]], candles: Optional[List[Dict[str, Any]]] = None) -> Optional[Signal]:
        if not candles or len(candles) < self.period:
            return None
            
        df = pd.DataFrame(candles)
        
        # Calcula Donchian
        df = self._calculate_donchian(df)
        
        current_candle = df.iloc[-1]
        
        # Upper e Lower atuais do Donchian
        d_upper = current_candle['donchian_upper']
        d_lower = current_candle['donchian_lower']
        
        # Preco atual e tempo
        current_price = ticks[-1]['quote'] if ticks else df.iloc[-1]['close']
        last_time = ticks[-1].get('epoch', 0) if ticks else df.iloc[-1]['time']
        
        if last_time <= self._last_signal_time:
            return None
        
        # O ZigZag em tempo real (Depth 15) garante que o Donchian (21) é um extremo absoluto.
        # Adicionamos a validação do Desvio (Deviation = 1%)
        
        is_touching_upper = current_price >= d_upper
        is_touching_lower = current_price <= d_lower
        
        from utils.indicators import calculate_zigzag
        
        zz = calculate_zigzag(candles, depth=15, deviation=self.zigzag_dev, backstep=3)
        if not zz or len(zz) < 2:
            return None
            
        last_pivot = zz[-2]
        current_leg_type = zz[-1]["type"]
        
        valido_para_put = False
        valido_para_call = False
        
        if is_touching_upper and last_pivot["type"] == "low" and current_leg_type == "high":
            if current_price >= last_pivot["value"] * (1 + self.zigzag_dev/100):
                valido_para_put = True
                
        if is_touching_lower and last_pivot["type"] == "high" and current_leg_type == "low":
            if current_price <= last_pivot["value"] * (1 - self.zigzag_dev/100):
                valido_para_call = True
        
        if valido_para_put:
            logger.info(f"Preço {current_price} tocou Donchian Upper e validou ZigZag 1%. Sinal PUT.")
            self._last_signal_time = last_time
            return Signal(action="PUT", reason=f"Tocou Donchian Sup. e variou 1% da minima", price=current_price, timestamp=last_time)
            
        if valido_para_call:
            logger.info(f"Preço {current_price} tocou Donchian Lower e validou ZigZag 1%. Sinal CALL.")
            self._last_signal_time = last_time
            return Signal(action="CALL", reason=f"Tocou Donchian Inf. e variou 1% da maxima", price=current_price, timestamp=last_time)

        return None
