from typing import Optional, List, Dict
from strategies.base import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("SimpleCallPutStrategy")

class SimpleCallPutStrategy(BaseStrategy):
    """
    Estrategia burra/mock. Alterna entre CALL e PUT a cada sinal gerado.
    Serve apenas para testar a maquina de estados (FSM) da Fase 1.
    """
    def __init__(self, stake: float = 1.0):
        self._stake = stake
        self._next_action = 'CALL'
        self._ticks_analyzed = 0

    def name(self) -> str:
        return "SimpleMockStrategy"
        
    def analyze(self, ticks: List[Dict]) -> Optional[Signal]:
        if not ticks:
            return None
            
        self._ticks_analyzed += 1
        
        # Gera um sinal a cada 5 ticks recebidos
        if self._ticks_analyzed >= 5:
            action = self._next_action
            
            # Alterna para a proxima
            self._next_action = 'PUT' if self._next_action == 'CALL' else 'CALL'
            self._ticks_analyzed = 0
            
            logger.info(f"Gerando sinal simulado: {action}")
            return Signal(action=action)
            
        return None

    def on_trade_result(self, result: dict) -> None:
        profit = result.get('profit', 0)
        status = result.get('status', 'unknown')
        logger.info(f"Estrategia notificada do resultado: {status} | PnL: {profit}")

    def get_stake(self) -> float:
        return self._stake

    def get_contract_params(self) -> dict:
        return {
            "duration": 5,
            "duration_unit": "t"
        }
