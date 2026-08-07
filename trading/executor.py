from utils.logger import setup_logger
from trading.safety import ensure_account_allowed

logger = setup_logger("OrderExecutor")


class AmbiguousBuyError(RuntimeError):
    """The transport failed after send, so acceptance cannot be inferred safely."""


AMBIGUOUS_ERROR_CODES = {"Timeout", "Disconnected", "SendError"}

class OrderExecutor:
    def __init__(self, connection, account_type="demo"):
        self.connection = connection
        self.account_type = account_type

    async def buy(self, proposal_id: str, price: float, passthrough: dict = None) -> dict:
        ensure_account_allowed(self.account_type)
        request = {
            "buy": proposal_id,
            "price": price
        }
        if passthrough:
            request["passthrough"] = dict(passthrough)
        
        logger.info(f"Enviando ordem de COMPRA... ID: {proposal_id} | Preco Max: ${price}")
        
        response = await self.connection.send(request)
        
        if 'error' in response:
            code = str(response['error'].get('code') or '')
            if code in AMBIGUOUS_ERROR_CODES:
                raise AmbiguousBuyError(
                    response['error'].get('message') or "Resultado da compra desconhecido"
                )
            logger.error(f"Rejeicao na compra: {response['error'].get('message', response['error'])}")
            return None
            
        if 'buy' in response:
            buy_data = response['buy']
            logger.info(f"COMPRA EFETIVADA! Contract ID: {buy_data.get('contract_id')}")
            logger.info(f"Saldo restante: {buy_data.get('balance_after')}")
            return buy_data
            
        return None
