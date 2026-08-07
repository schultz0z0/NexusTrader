from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from utils.logger import setup_logger

logger = setup_logger("StrategyBase")

@dataclass
class Signal:
    action: str  # "CALL" ou "PUT"
    reason: str
    price: float
    timestamp: int

class MoneyManager:
    """
    Gerencia o valor das entradas (Stake) usando estrategias como Martingale, Soros ou Mao Fixa.
    """
    def __init__(
        self,
        mode: str = "martingale",
        initial_stake: float = 1.0,
        martingale_multiplier: float = 2.0,
        max_martingale_levels: int = 3,
        soros_levels: int = 2,
        soros_percent: float = 0.5
    ):
        self.mode = mode.lower()
        self.initial_stake = initial_stake
        self.martingale_multiplier = martingale_multiplier
        self.max_martingale_levels = max_martingale_levels
        self.soros_levels = soros_levels
        self.soros_percent = soros_percent
        
        self.current_stake = initial_stake
        self.current_level = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0

    def update_config(self, max_martingale_levels: int, initial_stake: float = None):
        self.max_martingale_levels = max_martingale_levels
        if initial_stake is not None and initial_stake != self.initial_stake:
            self.initial_stake = initial_stake
            # Se nao estamos no meio de um Martingale, atualizamos a aposta atual
            if self.current_level == 0:
                self.current_stake = initial_stake

    def get_stake(self) -> float:
        return round(self.current_stake, 2)

    def restore_state(self, state: Dict[str, Any]):
        self.current_stake = float(state.get("current_stake", self.initial_stake))
        self.current_level = int(state.get("current_level", 0))
        self.consecutive_wins = int(state.get("consecutive_wins", 0))
        self.consecutive_losses = int(state.get("consecutive_losses", 0))

    def on_trade_result(self, is_win: bool, profit: float = 0.0):
        if is_win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            
            if self.mode == "martingale":
                logger.info(f"WIN! Resetando Martingale para o valor inicial: ${self.initial_stake}")
                self.current_stake = self.initial_stake
                self.current_level = 0
                
            elif self.mode == "soros":
                if self.consecutive_wins <= self.soros_levels:
                    # Soros: Entradas adicionais com lucro
                    added_stake = self.initial_stake + (profit * self.soros_percent)
                    logger.info(f"WIN (Soros Nivel {self.consecutive_wins})! Nova stake: ${added_stake:.2f}")
                    self.current_stake = added_stake
                else:
                    logger.info(f"WIN (Soros ciclo completo)! Resetando para ${self.initial_stake}")
                    self.current_stake = self.initial_stake
                    self.consecutive_wins = 0
            else: # fixed
                self.current_stake = self.initial_stake
                
        else: # LOSS
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            
            if self.mode == "martingale":
                self.current_level += 1
                if self.current_level <= self.max_martingale_levels:
                    new_stake = self.current_stake * self.martingale_multiplier
                    logger.warning(
                        f"LOSS! Martingale Nivel {self.current_level}/{self.max_martingale_levels}. "
                        f"Multiplicando stake ({self.current_stake} -> {new_stake:.2f})"
                    )
                    self.current_stake = new_stake
                else:
                    logger.error(
                        f"LOSS! Atingiu o limite maximo de Martingale ({self.max_martingale_levels} niveis). "
                        f"Resetando banca de seguranca para ${self.initial_stake}"
                    )
                    self.current_stake = self.initial_stake
                    self.current_level = 0
            else:
                logger.info(f"LOSS! Mantendo stake inicial: ${self.initial_stake}")
                self.current_stake = self.initial_stake

class BaseStrategy(ABC):
    """
    Classe Abstrata para todas as Estrategias de Trading do NexusTrader.
    """
    def __init__(self, money_manager: Optional[MoneyManager] = None):
        self.money_manager = money_manager or MoneyManager()

    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def analyze(self, ticks: List[Dict[str, Any]], candles: Optional[List[Dict[str, Any]]] = None) -> Optional[Signal]:
        pass

    def on_trade_result(self, poc_data: Dict[str, Any]):
        status = poc_data.get('status')
        profit = float(poc_data.get('profit', 0))
        is_win = (status == 'won') or (profit > 0)
        self.money_manager.on_trade_result(is_win=is_win, profit=profit)

    def get_stake(self) -> float:
        return self.money_manager.get_stake()

    def get_contract_params(self) -> dict:
        return {"duration": 5, "duration_unit": "t"}
