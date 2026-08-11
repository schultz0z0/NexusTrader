import httpx
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("AuthManager")

class AuthManager:
    """
    Gerencia a autenticacao com a Nova API da Deriv (REST + OTP).
    
    Fluxo:
    1. Usa o PAT token nos headers de autorizacao REST
    2. Lista contas via GET /trading/v1/options/accounts
    3. Solicita OTP via POST /trading/v1/options/accounts/{accountId}/otp
    4. Retorna a URL WebSocket pre-autenticada (valida por 120s)
    """
    def __init__(self):
        self._is_authorized = False
        self._account_info = None
        self._accounts = []
        self._headers = {
            "Authorization": f"Bearer {settings.DERIV_API_TOKEN}",
            "Deriv-App-ID": settings.DERIV_APP_ID,
            "Content-Type": "application/json"
        }
        self._client = httpx.AsyncClient(
            base_url=settings.DERIV_REST_BASE_URL,
            headers=self._headers,
            timeout=30.0
        )

    async def check_health(self) -> bool:
        """Verifica se o servidor da API esta online."""
        try:
            response = await self._client.get("/v1/health")
            if response.status_code == 200:
                logger.info("Servidor Deriv API esta online (Health OK).")
                return True
            else:
                logger.error(f"Health check falhou: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error("Health check falhou (error_type=%s).", type(e).__name__)
            return False

    async def list_accounts(self) -> list:
        """Lista as contas de trading disponiveis via REST API."""
        try:
            logger.info("Consultando catalogo de contas Deriv.")
            response = await self._client.get("/trading/v1/options/accounts")
            
            if response.status_code == 200:
                data = response.json()
                self._accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', [data]))
                account_count = len(self._accounts) if isinstance(self._accounts, list) else 1
                logger.info("Catalogo Deriv recebido (accounts=%s).", account_count)
                    
                self._is_authorized = True
                return self._accounts
            elif response.status_code == 401:
                logger.error("Token invalido ou expirado (HTTP 401).")
                return []
            else:
                logger.error(
                    "Falha no catalogo Deriv (http_status=%s).",
                    response.status_code,
                )
                return []
                
        except Exception as e:
            logger.error(
                "Falha no catalogo Deriv (error_type=%s).",
                type(e).__name__,
            )
            return []

    async def get_websocket_url(self, account_id: str = None) -> str:
        """
        Solicita OTP e retorna a URL WebSocket pre-autenticada.
        Essa URL eh valida por 120 segundos.
        """
        acc_id = account_id or settings.DERIV_ACCOUNT_ID
        
        if not acc_id:
            logger.error("Account ID nao configurado. Defina DERIV_ACCOUNT_ID no .env")
            return None
            
        try:
            logger.info("Solicitando OTP Deriv para conta selecionada.")
            response = await self._client.post(
                f"/trading/v1/options/accounts/{acc_id}/otp"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # A resposta vem no formato: {"data": {"url": "wss://..."}}
                ws_url = None
                if isinstance(data, dict):
                    if 'data' in data and isinstance(data['data'], dict):
                        ws_url = data['data'].get('url')
                    elif 'websocket_url' in data:
                        ws_url = data['websocket_url']
                    elif 'url' in data:
                        ws_url = data['url']
                
                if ws_url:
                    logger.info("URL WebSocket pre-autenticada recebida com sucesso!")
                    return ws_url
                else:
                    logger.error("Resposta OTP invalida (shape=missing_url).")
                    return None
            else:
                logger.error(
                    "Falha ao solicitar OTP (http_status=%s).",
                    response.status_code,
                )
                return None
                
        except Exception as e:
            logger.error(
                "Falha ao solicitar OTP (error_type=%s).",
                type(e).__name__,
            )
            return None

    def is_authorized(self) -> bool:
        return self._is_authorized

    def get_accounts(self) -> list:
        return self._accounts

    async def close(self):
        await self._client.aclose()
