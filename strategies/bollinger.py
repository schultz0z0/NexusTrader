import numpy as np
import pandas as pd
from typing import Optional, List, Dict, Any
from strategies.base import BaseStrategy, Signal, MoneyManager
from utils.logger import setup_logger

logger = setup_logger("BollingerStrategy")

class BollingerBandsStrategy(BaseStrategy):
    """
    Estrategia de Bandas de Bollinger com reversao a media.
    Gera sinais de CALL quando o preco toca/rompe a banda inferior e PUT na banda superior.
    """
    def __init__(
        self,
        period: int = 20,
        std_dev: float = 2.0,
        money_manager: Optional[MoneyManager] = None,
        duration: int = 5,
        duration_unit: str = "t"
    ):
        super().__init__(money_manager or MoneyManager(mode="martingale", initial_stake=1.0, max_martingale_levels=3))
        self.period = period
        self.std_dev = std_dev
        self.duration = duration
        self.duration_unit = duration_unit
        self._last_signal_time = 0

    def name(self) -> str:
        return f"BollingerBands({self.period}, {self.std_dev})"

    def calculate_bands(self, prices: pd.Series):
        """Calcula a Média Móvel, Banda Superior e Banda Inferior."""
        sma = prices.rolling(window=self.period).mean()
        std = prices.rolling(window=self.period).std()
        upper = sma + (std * self.std_dev)
        lower = sma - (std * self.std_dev)
        return sma, upper, lower

    def analyze(self, ticks: List[Dict[str, Any]], candles: Optional[List[Dict[str, Any]]] = None) -> Optional[Signal]:
        # Precisamos de pelo menos 'period' pontos de dados
        if len(ticks) < self.period:
            return None

        # Extrair serie de precos
        quotes = [t['quote'] for t in ticks]
        prices = pd.Series(quotes)
        
        sma, upper, lower = self.calculate_bands(prices)
        
        last_price = prices.iloc[-1]
        last_upper = upper.iloc[-1]
        last_lower = lower.iloc[-1]
        last_sma = sma.iloc[-1]
        last_time = ticks[-1].get('epoch', 0)

        # Evita sinal duplicado no mesmo tick
        if last_time <= self._last_signal_time:
            return None

        if pd.isna(last_upper) or pd.isna(last_lower):
            return None

        logger.debug(
            f"BB Check [{self.name()}] - Preco: {last_price:.2f} | "
            f"Upper: {last_upper:.2f} | SMA: {last_sma:.2f} | Lower: {last_lower:.2f}"
        )

        # Sinal CALL: Preco abaixo ou tocando a Banda Inferior
        if last_price <= last_lower:
            self._last_signal_time = last_time
            reason = f"Preco ({last_price:.2f}) rompeu a Banda Inferior ({last_lower:.2f})"
            logger.info(f"[CALL] SINAL CALL GERADO! {reason}")
            return Signal(action="CALL", reason=reason, price=last_price, timestamp=last_time)

        # Sinal PUT: Preco acima ou tocando a Banda Superior
        elif last_price >= last_upper:
            self._last_signal_time = last_time
            reason = f"Preco ({last_price:.2f}) rompeu a Banda Superior ({last_upper:.2f})"
            logger.info(f"[PUT] SINAL PUT GERADO! {reason}")
            return Signal(action="PUT", reason=reason, price=last_price, timestamp=last_time)

        return None

    def get_contract_params(self) -> dict:
        return {"duration": self.duration, "duration_unit": self.duration_unit}
