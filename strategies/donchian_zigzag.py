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
        self.zigzag_dev = 1.0
        self.zigzag_depth = 15
        self.zigzag_backstep = 3
        self.duration = 2
        self.duration_unit = 'm'
        self._last_signal_key = None
        
        logger.info("Estrategia Donchian ZigZag inicializada.")

    def name(self) -> str:
        return f"DonchianZigZag({self.period}, {self.zigzag_dev})"

    def get_contract_params(self) -> dict:
        return {"duration": self.duration, "duration_unit": self.duration_unit}

    def _calculate_donchian(self, df: pd.DataFrame) -> pd.DataFrame:
        calculated = df.copy()
        calculated['donchian_upper'] = calculated['high'].rolling(window=self.period).max()
        calculated['donchian_lower'] = calculated['low'].rolling(window=self.period).min()
        return calculated
        
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
        last_time = int(ticks[-1].get('epoch', 0) if ticks else current_candle['time'])
        
        from utils.indicators import calculate_zigzag
        
        zz = calculate_zigzag(
            candles,
            depth=self.zigzag_depth,
            deviation=self.zigzag_dev,
            backstep=self.zigzag_backstep,
        )
        if not zz or len(zz) < 2:
            return None

        previous_pivot, current_tip = zz[-2], zz[-1]
        current_candle_time = int(current_candle['time'])
        tip_time = int(current_tip['time'])
        signal_key = (tip_time, current_tip['type'])

        # A confluencia deve existir agora. Um extremo historico nao pode disparar uma
        # ordem tardia apenas porque o ultimo tick alcançou uma banda antiga.
        if tip_time != current_candle_time or signal_key == self._last_signal_key:
            return None

        tip_value = float(current_tip['value'])
        valid_put = (
            current_tip['type'] == 'high'
            and previous_pivot['type'] == 'low'
            and tip_value >= float(d_upper)
        )
        valid_call = (
            current_tip['type'] == 'low'
            and previous_pivot['type'] == 'high'
            and tip_value <= float(d_lower)
        )

        if valid_put:
            logger.info(
                "Ponta ZigZag HIGH %.5f tocou Donchian Upper %.5f. Sinal PUT.",
                tip_value,
                d_upper,
            )
            self._last_signal_key = signal_key
            return Signal(
                action="PUT",
                reason="ZigZag HIGH tocou Donchian superior (reversao)",
                price=float(current_price),
                timestamp=last_time,
            )

        if valid_call:
            logger.info(
                "Ponta ZigZag LOW %.5f tocou Donchian Lower %.5f. Sinal CALL.",
                tip_value,
                d_lower,
            )
            self._last_signal_key = signal_key
            return Signal(
                action="CALL",
                reason="ZigZag LOW tocou Donchian inferior (reversao)",
                price=float(current_price),
                timestamp=last_time,
            )

        return None
