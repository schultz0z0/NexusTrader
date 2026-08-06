import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

import websockets

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("DerivConnection")


MessageHandler = Callable[[dict], Awaitable[None]]


@dataclass
class SubscriptionRecord:
    key: str
    request: dict
    handler: MessageHandler
    remote_id: Optional[str] = None


class NexusConnection:
    """Account-scoped Deriv WebSocket with supervised reconnects."""

    def __init__(
        self,
        auth_manager,
        connector=None,
        reconnect_delays=(1, 2, 4, 8, 16, 30),
        heartbeat_interval=30,
    ):
        self.auth_manager = auth_manager
        self._connector = connector or websockets.connect
        self._reconnect_delays = tuple(reconnect_delays) or (1,)
        self._heartbeat_interval = heartbeat_interval
        self.ws = None
        self.websocket_url = None
        self.account_id = None
        self._is_connected = False
        self._closing = False
        self._listener_task = None
        self._reconnect_task = None
        self._heartbeat_task = None
        self._handler_tasks = set()
        self._subscriptions: Dict[str, SubscriptionRecord] = {}
        self._pending_requests = {}
        self._pending_subscription_keys = {}
        self._req_id_counter = 0
        self._send_lock = asyncio.Lock()
        self._connected_event = asyncio.Event()
        self._generation = 0

    async def connect(self, account_id: str = None) -> bool:
        if self._is_connected:
            return True
        selected_account = account_id or self.account_id or settings.DERIV_ACCOUNT_ID
        if not selected_account:
            logger.error("Account ID nao configurado para a conexao Deriv.")
            return False
        self.account_id = selected_account
        self._closing = False
        try:
            await self._open_socket(replay_subscriptions=False)
            return True
        except Exception as exc:
            logger.error(f"Erro ao conectar WebSocket: {exc}")
            self._is_connected = False
            self._connected_event.clear()
            return False

    async def _open_socket(self, replay_subscriptions: bool):
        websocket_url = await self.auth_manager.get_websocket_url(self.account_id)
        if not websocket_url:
            raise ConnectionError(f"OTP indisponivel para a conta {self.account_id}")

        logger.info(f"Conectando ao WebSocket da conta {self.account_id}...")
        self.websocket_url = websocket_url
        self.ws = await self._connector(websocket_url, ping_interval=30, ping_timeout=10)
        self._is_connected = True
        self._listener_task = asyncio.create_task(self._message_listener())

        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        if replay_subscriptions:
            for record in list(self._subscriptions.values()):
                record.remote_id = None
                await self._activate_subscription(record)

        self._generation += 1
        self._connected_event.set()
        logger.info(
            f"Conexao Deriv pronta (geracao {self._generation}, "
            f"subscriptions: {len(self._subscriptions)})."
        )

    async def _message_listener(self):
        try:
            async for raw_message in self.ws:
                data = json.loads(raw_message)
                req_id = data.get("req_id")
                subscription_key = self._pending_subscription_keys.pop(req_id, None)

                if subscription_key and subscription_key in self._subscriptions:
                    remote_id = data.get("subscription", {}).get("id")
                    if remote_id:
                        self._subscriptions[subscription_key].remote_id = remote_id

                future = self._pending_requests.pop(req_id, None)
                if future and not future.done():
                    future.set_result(data)

                record = None
                if subscription_key:
                    record = self._subscriptions.get(subscription_key)
                if record is None:
                    remote_id = data.get("subscription", {}).get("id")
                    if remote_id:
                        record = next(
                            (item for item in self._subscriptions.values() if item.remote_id == remote_id),
                            None,
                        )
                if record is None:
                    msg_type = data.get("msg_type")
                    matches = [
                        item for item in self._subscriptions.values()
                        if self._get_expected_msg_type(item.request) == msg_type
                    ]
                    if len(matches) == 1:
                        record = matches[0]
                if record:
                    task = asyncio.create_task(self._run_handler(record, data))
                    self._handler_tasks.add(task)
                    task.add_done_callback(self._handler_tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                logger.warning(f"Conexao WebSocket interrompida: {exc}")
                await self._handle_connection_loss(exc)

    async def _run_handler(self, record, data):
        try:
            await record.handler(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"Falha no handler da subscription {record.key}: {exc}")

    async def _handle_connection_loss(self, exc):
        self._is_connected = False
        self._connected_event.clear()
        self._fail_pending(ConnectionError(str(exc)))
        if not self._closing and (self._reconnect_task is None or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_flow())

    def _fail_pending(self, exc):
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(exc)
        self._pending_requests.clear()
        self._pending_subscription_keys.clear()

    async def send(self, request: dict, timeout: float = 30.0, subscription_key: str = None) -> dict:
        if not self._is_connected or self.ws is None:
            return {"error": {"code": "NotConnected", "message": "WebSocket not connected"}}

        async with self._send_lock:
            self._req_id_counter += 1
            req_id = self._req_id_counter
            payload = dict(request)
            payload["req_id"] = req_id
            future = asyncio.get_running_loop().create_future()
            self._pending_requests[req_id] = future
            if subscription_key:
                self._pending_subscription_keys[req_id] = subscription_key
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as exc:
                self._pending_requests.pop(req_id, None)
                self._pending_subscription_keys.pop(req_id, None)
                await self._handle_connection_loss(exc)
                return {"error": {"code": "SendError", "message": str(exc)}}

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            self._pending_subscription_keys.pop(req_id, None)
            return {"error": {"code": "Timeout", "message": "Request timed out"}}
        except ConnectionError as exc:
            return {"error": {"code": "Disconnected", "message": str(exc)}}

    async def subscribe(self, key, request=None, handler=None) -> str:
        # Compatibility with the original subscribe(request, handler) signature.
        if isinstance(key, dict):
            handler = request
            request = key
            base = self._get_expected_msg_type(request) or "stream"
            target = request.get("ticks") or request.get("contract_id") or len(self._subscriptions)
            key = f"{base}:{target}"
        if not callable(handler):
            raise TypeError("handler assincrono e obrigatorio")
        record = SubscriptionRecord(
            key=str(key),
            request={**dict(request), "subscribe": 1},
            handler=handler,
        )
        self._subscriptions[record.key] = record
        await self._activate_subscription(record)
        return record.remote_id

    async def _activate_subscription(self, record: SubscriptionRecord):
        response = await self.send(record.request, subscription_key=record.key)
        if "error" in response:
            raise ConnectionError(response["error"].get("message", str(response["error"])))
        record.remote_id = response.get("subscription", {}).get("id") or record.remote_id

    async def unsubscribe(self, key: str):
        record = self._subscriptions.pop(key, None)
        if record and record.remote_id and self._is_connected:
            await self.send({"forget": record.remote_id})

    async def forget(self, subscription_id: str):
        return await self.send({"forget": subscription_id})

    async def forget_all(self, msg_type: str):
        return await self.send({"forget_all": msg_type})

    async def _reconnect_flow(self):
        attempt = 0
        while not self._closing and not self._is_connected:
            delay = self._reconnect_delays[min(attempt, len(self._reconnect_delays) - 1)]
            attempt += 1
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._open_socket(replay_subscriptions=True)
                logger.info("Conexao Deriv e subscriptions restauradas.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Falha na reconexao Deriv #{attempt}: {exc}")
                self._is_connected = False
                self._connected_event.clear()

    async def _heartbeat_loop(self):
        try:
            while not self._closing:
                await asyncio.sleep(self._heartbeat_interval)
                if self._is_connected:
                    response = await self.send({"ping": 1}, timeout=10)
                    if "error" in response and response["error"].get("code") != "NotConnected":
                        logger.warning(f"Heartbeat Deriv falhou: {response['error']}")
        except asyncio.CancelledError:
            raise

    async def wait_until_connected(self, timeout=30, minimum_generation=1):
        async def wait_for_generation():
            while self._generation < minimum_generation or not self._is_connected:
                await self._connected_event.wait()
                if self._generation < minimum_generation:
                    self._connected_event.clear()
                    await asyncio.sleep(0)
            return True

        return await asyncio.wait_for(wait_for_generation(), timeout=timeout)

    @staticmethod
    def _get_expected_msg_type(request: dict) -> str:
        for field, msg_type in (
            ("ticks", "tick"),
            ("ticks_history", "history"),
            ("proposal", "proposal"),
            ("proposal_open_contract", "proposal_open_contract"),
            ("balance", "balance"),
            ("transaction", "transaction"),
        ):
            if field in request:
                return msg_type
        return ""

    async def disconnect(self):
        self._closing = True
        self._is_connected = False
        self._connected_event.clear()
        self._fail_pending(ConnectionError("Connection closed"))

        tasks = [self._reconnect_task, self._heartbeat_task, self._listener_task]
        tasks.extend(self._handler_tasks)
        for task in tasks:
            if task and not task.done():
                task.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        await asyncio.gather(*(task for task in tasks if task), return_exceptions=True)
        await self.auth_manager.close()

    @property
    def is_connected(self):
        return self._is_connected
