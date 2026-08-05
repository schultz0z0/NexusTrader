from utils.logger import setup_logger

logger = setup_logger("ProposalManager")

class ProposalManager:
    """
    Gerencia a solicitacao e validacao de cotacoes (proposals) antes da compra.
    """
    def __init__(self, connection):
        self.connection = connection

    async def request_proposal(self, symbol: str, contract_type: str, stake: float, duration: int, duration_unit: str = 't') -> dict:
        request = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": duration_unit,
            "underlying_symbol": symbol
        }
        
        logger.info(f"Solicitando proposal: {contract_type} em {symbol} | Stake: ${stake} | Duracao: {duration}{duration_unit}")
        
        response = await self.connection.send(request)
        
        if 'error' in response:
            logger.error(f"Erro na proposal: {response['error'].get('message', response['error'])}")
            return None
            
        if 'proposal' in response:
            proposal_data = response['proposal']
            logger.info(f"Proposal recebida! ID: {proposal_data.get('id')} | Payout: {proposal_data.get('payout')}")
            return proposal_data
            
        return None
