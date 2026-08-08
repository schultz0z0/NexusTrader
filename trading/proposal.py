from utils.logger import setup_logger

logger = setup_logger("ProposalManager")

class ProposalManager:
    """
    Gerencia a solicitacao e validacao de cotacoes (proposals) antes da compra.
    """
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def profit_ratio(proposal: dict):
        """Return net profit divided by stake, or None for unusable quotes."""
        try:
            ask_price = float(proposal["ask_price"])
            payout = float(proposal["payout"])
        except (KeyError, TypeError, ValueError):
            return None
        if ask_price <= 0:
            return None
        return (payout - ask_price) / ask_price

    async def validate_contract_types(self, symbol: str, required_types) -> bool:
        """Fail closed when a symbol does not advertise every required contract."""
        response = await self.connection.send({"contracts_for": symbol})
        if not isinstance(response, dict) or "error" in response:
            error = response.get("error") if isinstance(response, dict) else response
            raise ValueError(f"Nao foi possivel validar contratos de {symbol}: {error}")
        available = {
            item.get("contract_type")
            for item in response.get("contracts_for", {}).get("available", [])
            if isinstance(item, dict)
        }
        missing = sorted(set(required_types) - available)
        if missing:
            raise ValueError(
                f"Contratos indisponiveis em {symbol}: {', '.join(missing)}"
            )
        return True

    async def validate_fixed_duration(
        self,
        symbol: str,
        required_types,
        stake: float,
        duration: int,
        duration_unit: str,
    ) -> bool:
        """Confirm the exact duration by obtaining a real quote for each side."""
        for contract_type in sorted(required_types):
            proposal = await self.request_proposal(
                symbol,
                contract_type,
                stake,
                duration,
                duration_unit,
            )
            if not proposal or not proposal.get("id"):
                raise ValueError(
                    f"Contrato {contract_type} {duration}{duration_unit} "
                    f"indisponivel em {symbol}"
                )
        return True

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
