import asyncio
from utils.logger import setup_logger

logger = setup_logger("ContractMonitor")

class ContractMonitor:
    def __init__(self, connection):
        self.connection = connection

    async def monitor_contract(self, contract_id: int, on_settled_callback) -> None:
        logger.info(f"Iniciando monitoramento do contrato {contract_id}...")
        
        request = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
        }
        
        async def on_update(data):
            if 'proposal_open_contract' in data:
                poc = data['proposal_open_contract']
                is_sold = poc.get('is_sold')
                
                if is_sold == 1:
                    status = poc.get('status', 'unknown')
                    profit = poc.get('profit', 0)
                    logger.info(f"CONTRATO ENCERRADO! ID: {contract_id} | Resultado: {status} | Lucro: {profit}")
                    if on_settled_callback:
                        await on_settled_callback(poc)
                else:
                    pnl = poc.get('profit', 0)
                    logger.debug(f"Contrato {contract_id} ABERTO. PnL Atual: {pnl}")

        await self.connection.subscribe(request, on_update)
