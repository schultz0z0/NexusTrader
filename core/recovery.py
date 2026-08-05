from typing import Optional, List, Dict, Any
from utils.logger import setup_logger

logger = setup_logger("CrashRecovery")

class CrashRecoveryHandler:
    """
    Verifica e recupera contratos abertos na Deriv caso o bot seja reiniciado.
    """
    def __init__(self, connection):
        self.connection = connection

    async def check_open_contracts(self) -> List[Dict[str, Any]]:
        """Solicita a lista de posicoes abertas no portfolio."""
        logger.info("CrashRecovery: Verificando ordens abertas na conta...")
        request = {"portfolio": 1}
        
        response = await self.connection.send(request)
        open_contracts = []
        
        if 'portfolio' in response and 'contracts' in response['portfolio']:
            contracts = response['portfolio']['contracts']
            for c in contracts:
                logger.warning(
                    f"⚠️ CRASH RECOVERY: Ordem Aberta Encontrada! "
                    f"ID: {c.get('contract_id')} | Tipo: {c.get('contract_type')} | Stake: ${c.get('buy_price')}"
                )
                open_contracts.append(c)
        else:
            logger.info("CrashRecovery: Nenhuma ordem aberta pendente no portfolio.")
            
        return open_contracts
