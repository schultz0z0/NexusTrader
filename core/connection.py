import asyncio
import json
import websockets
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("DerivConnection")

class NexusConnection:
    """
    Gerencia a conexao WebSocket com a Deriv usando o novo fluxo OTP.
    
    Fluxo:
    1. AuthManager solicita OTP via REST e retorna uma URL WebSocket pre-autenticada
    2. NexusConnection conecta nessa URL
    3. Envia/recebe mensagens JSON
    4. Reconecta automaticamente caso a conexao caia (solicita novo OTP)
    """
    def __init__(self, auth_manager):
        self.auth_manager = auth_manager
        self.ws = None
        self._is_connected = False
        self._reconnect_task = None
        self._message_handlers = {}
        self._pending_requests = {}
        self._req_id_counter = 0
        self._listener_task = None
        
    async def connect(self) -> bool:
        """Conecta via OTP WebSocket URL."""
        try:
            # Passo 1: Solicitar OTP via REST
            ws_url = await self.auth_manager.get_websocket_url()
            
            if not ws_url:
                logger.error("Nao foi possivel obter URL WebSocket (OTP falhou).")
                return False
            
            # Passo 2: Conectar na URL pre-autenticada
            logger.info("Conectando ao WebSocket pre-autenticado...")
            self.ws = await websockets.connect(ws_url, ping_interval=30, ping_timeout=10)
            self._is_connected = True
            logger.info("Conexao WebSocket estabelecida com sucesso!")
            
            # Passo 3: Iniciar listener de mensagens
            self._listener_task = asyncio.create_task(self._message_listener())
            
            return True
            
        except Exception as e:
            logger.error(f"Erro ao conectar WebSocket: {str(e)}")
            self._is_connected = False
            return False

    async def _message_listener(self):
        """Loop que escuta todas as mensagens do WebSocket."""
        try:
            async for message in self.ws:
                data = json.loads(message)
                msg_type = data.get('msg_type', '')
                req_id = data.get('req_id')
                
                # Se tiver req_id, resolve a promise pendente
                if req_id and req_id in self._pending_requests:
                    future = self._pending_requests.pop(req_id)
                    if not future.done():
                        future.set_result(data)
                
                # Se tiver handler registrado para o msg_type, chama
                if msg_type in self._message_handlers:
                    for handler in self._message_handlers[msg_type]:
                        asyncio.create_task(handler(data))
                        
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"Conexao WebSocket fechada: {e}")
            self._is_connected = False
            asyncio.create_task(self._reconnect_flow())
        except Exception as e:
            logger.error(f"Erro no listener: {str(e)}")
            self._is_connected = False

    async def send(self, request: dict, timeout: float = 30.0) -> dict:
        """Envia uma mensagem e aguarda a resposta (request-response)."""
        if not self._is_connected or not self.ws:
            logger.error("Nao esta conectado. Impossivel enviar mensagem.")
            return {"error": {"code": "NotConnected", "message": "WebSocket not connected"}}
        
        # Atribui req_id unico
        self._req_id_counter += 1
        req_id = self._req_id_counter
        request['req_id'] = req_id
        
        # Cria future para a resposta
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[req_id] = future
        
        try:
            await self.ws.send(json.dumps(request))
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Timeout esperando resposta para req_id {req_id}")
            return {"error": {"code": "Timeout", "message": "Request timed out"}}
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Erro ao enviar: {str(e)}")
            return {"error": {"code": "SendError", "message": str(e)}}

    async def subscribe(self, request: dict, handler) -> str:
        """Envia um request com subscribe=1 e registra um handler para as atualizacoes."""
        request['subscribe'] = 1
        
        msg_type = self._get_expected_msg_type(request)
        if msg_type:
            if msg_type not in self._message_handlers:
                self._message_handlers[msg_type] = []
            self._message_handlers[msg_type].append(handler)
        
        response = await self.send(request)
        
        subscription_id = None
        if 'subscription' in response:
            subscription_id = response['subscription'].get('id')
            
        return subscription_id

    def _get_expected_msg_type(self, request: dict) -> str:
        """Infere o msg_type esperado a partir do request."""
        if 'ticks' in request:
            return 'tick'
        elif 'proposal' in request:
            return 'proposal'
        elif 'proposal_open_contract' in request:
            return 'proposal_open_contract'
        elif 'balance' in request:
            return 'balance'
        elif 'transaction' in request:
            return 'transaction'
        return ''

    async def forget(self, subscription_id: str):
        """Cancela uma subscription especifica."""
        return await self.send({"forget": subscription_id})

    async def forget_all(self, msg_type: str):
        """Cancela todas subscriptions de um tipo."""
        return await self.send({"forget_all": msg_type})

    async def _reconnect_flow(self):
        """Reconecta com exponential backoff."""
        attempt = 1
        max_delay = 30
        
        while not self._is_connected:
            delay = min(2 ** attempt, max_delay)
            logger.info(f"Tentando reconectar em {delay}s (Tentativa {attempt})...")
            await asyncio.sleep(delay)
            
            try:
                success = await self.connect()
                if success:
                    logger.info("Reconectado com sucesso!")
                    return
            except Exception as e:
                logger.error(f"Falha na tentativa {attempt}: {str(e)}")
                
            attempt += 1

    async def disconnect(self):
        """Encerra a conexao WebSocket."""
        logger.info("Encerrando conexao com a Deriv API...")
        self._is_connected = False
        
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            
        if self.ws:
            await self.ws.close()
            
        await self.auth_manager.close()
        logger.info("Desconectado.")

    @property
    def is_connected(self):
        return self._is_connected
