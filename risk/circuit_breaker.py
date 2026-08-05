import time
from typing import Tuple, Dict, Any
from utils.logger import setup_logger

logger = setup_logger("CircuitBreaker")

class CircuitBreaker:
    """
    Disjuntor de Seguranca do NexusTrader.
    Pausa temporariamente as operacoes se o bot sofrer N losses consecutivos.
    """
    def __init__(self, config: Dict[str, Any] = None):
        self.update_config(config or {})
        self.consecutive_losses = 0
        self.tripped_at = 0.0

    def update_config(self, config: Dict[str, Any]):
        new_max = int(config.get("max_consecutive_losses", 3))
        new_cooldown = int(config.get("cooldown_minutes", 15)) * 60
        
        if not hasattr(self, "max_consecutive_losses") or (
            self.max_consecutive_losses != new_max or
            self.cooldown_seconds != new_cooldown
        ):
            self.max_consecutive_losses = new_max
            self.cooldown_seconds = new_cooldown
            logger.info(f"Configuracao CircuitBreaker Carregada: Max Losses: {self.max_consecutive_losses} | Cooldown: {self.cooldown_seconds}s")

    def record_result(self, is_win: bool):
        if is_win:
            self.consecutive_losses = 0
            self.tripped_at = 0.0
        else:
            self.consecutive_losses += 1
            logger.warning(f"CircuitBreaker: Loss consecutivo #{self.consecutive_losses}/{self.max_consecutive_losses}")
            
            if self.consecutive_losses > self.max_consecutive_losses:
                self.tripped_at = time.time()
                logger.error(
                    f"[CIRCUIT BREAKER] DISPARADO! {self.consecutive_losses} losses consecutivos. "
                    f"Operacoes pausadas por {self.cooldown_seconds // 60} minutos para protecao da banca."
                )

    def is_tripped(self) -> Tuple[bool, int]:
        if self.tripped_at == 0.0:
            return False, 0
            
        elapsed = time.time() - self.tripped_at
        remaining = int(self.cooldown_seconds - elapsed)
        
        if remaining > 0:
            return True, remaining
        else:
            # Cool-down finalizado
            logger.info("CircuitBreaker: Perio de Cool-down encerrado. Retomando operacoes...")
            self.tripped_at = 0.0
            self.consecutive_losses = 0
            return False, 0
