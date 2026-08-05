from enum import Enum, auto
from utils.logger import setup_logger

logger = setup_logger("StateMachine")

class TradingState(Enum):
    IDLE = auto()
    ANALYZING = auto()
    SIGNAL_GENERATED = auto()
    REQUESTING_PROPOSAL = auto()
    PROPOSAL_RECEIVED = auto()
    PURCHASING = auto()
    CONTRACT_ACTIVE = auto()
    CONTRACT_SETTLED = auto()
    PROCESSING_RESULT = auto()
    ERROR = auto()

class TradingStateMachine:
    """
    Maquina de Estados Finitos (FSM) isolada para o fluxo de trading.
    Garante transicoes consistentes evitando estados invalidos ou ordens duplicadas.
    """
    def __init__(self):
        self.state = TradingState.IDLE

    def can_transition(self, new_state: TradingState) -> bool:
        """Verifica se a transicao eh permitida baseada nas regras pre-definidas."""
        # Se for erro, sempre pode
        if new_state == TradingState.ERROR:
            return True
            
        transitions = {
            TradingState.IDLE: [TradingState.ANALYZING],
            TradingState.ANALYZING: [TradingState.SIGNAL_GENERATED, TradingState.IDLE],
            TradingState.SIGNAL_GENERATED: [TradingState.REQUESTING_PROPOSAL, TradingState.IDLE],
            TradingState.REQUESTING_PROPOSAL: [TradingState.PROPOSAL_RECEIVED, TradingState.ERROR],
            TradingState.PROPOSAL_RECEIVED: [TradingState.PURCHASING, TradingState.IDLE],
            TradingState.PURCHASING: [TradingState.CONTRACT_ACTIVE, TradingState.ERROR],
            TradingState.CONTRACT_ACTIVE: [TradingState.CONTRACT_SETTLED],
            TradingState.CONTRACT_SETTLED: [TradingState.PROCESSING_RESULT],
            TradingState.PROCESSING_RESULT: [TradingState.IDLE],
            TradingState.ERROR: [TradingState.IDLE]
        }
        
        allowed = transitions.get(self.state, [])
        return new_state in allowed

    def transition_to(self, new_state: TradingState) -> bool:
        if self.can_transition(new_state):
            logger.info(f"FSM Transicao: {self.state.name} -> {new_state.name}")
            self.state = new_state
            return True
        else:
            logger.warning(f"Transicao invalida rejeitada: {self.state.name} -> {new_state.name}")
            return False

    def reset(self):
        logger.info("Resetando FSM para IDLE.")
        self.state = TradingState.IDLE
