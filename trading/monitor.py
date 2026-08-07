import asyncio
import time

from config.settings import settings
from utils.logger import setup_logger


logger = setup_logger("ContractMonitor")


class ContractMonitor:
    def __init__(
        self,
        connection,
        reconcile_interval_seconds=None,
        expiry_grace_seconds=None,
    ):
        self.connection = connection
        self.reconcile_interval_seconds = float(
            settings.CONTRACT_RECONCILE_INTERVAL_SECONDS
            if reconcile_interval_seconds is None
            else reconcile_interval_seconds
        )
        self.expiry_grace_seconds = float(
            settings.CONTRACT_EXPIRY_GRACE_SECONDS
            if expiry_grace_seconds is None
            else expiry_grace_seconds
        )
        self._settled_contracts = set()
        self._settlement_locks = {}
        self._reconciliation_tasks = {}
        self._expiry_events = {}
        self._expiry_times = {}
        self._subscription_keys = set()
        self._closed = False

    async def monitor_contract(
        self,
        contract_id: int,
        on_settled_callback,
        on_update_callback=None,
    ) -> None:
        if self._closed:
            return

        logger.info(f"Iniciando monitoramento do contrato {contract_id}...")
        subscription_key = f"contract:{contract_id}"
        request = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
        }
        self._expiry_events.setdefault(contract_id, asyncio.Event())
        self._subscription_keys.add(subscription_key)

        async def on_update(data):
            await self._handle_contract_payload(
                contract_id,
                data,
                on_settled_callback,
                on_update_callback,
            )

        try:
            await self.connection.subscribe(subscription_key, request, on_update)
        except Exception:
            self._subscription_keys.discard(subscription_key)
            self._expiry_events.pop(contract_id, None)
            raise

        existing_task = self._reconciliation_tasks.get(contract_id)
        if (
            not self._closed
            and contract_id not in self._settled_contracts
            and (existing_task is None or existing_task.done())
        ):
            self._reconciliation_tasks[contract_id] = asyncio.create_task(
                self._reconcile_contract(
                    contract_id,
                    on_settled_callback,
                    on_update_callback,
                )
            )

    async def _handle_contract_payload(
        self,
        contract_id,
        data,
        on_settled_callback,
        on_update_callback,
    ):
        if self._closed or not isinstance(data, dict):
            return
        poc = data.get("proposal_open_contract")
        if not poc or int(poc.get("contract_id", -1)) != int(contract_id):
            return

        if poc.get("date_expiry") is not None:
            self._expiry_times[contract_id] = float(poc["date_expiry"])
            expiry_event = self._expiry_events.get(contract_id)
            if expiry_event:
                expiry_event.set()

        if poc.get("is_sold") != 1:
            if on_update_callback:
                await on_update_callback(poc)
            logger.debug(
                f"Contrato {contract_id} ABERTO. PnL Atual: {poc.get('profit', 0)}"
            )
            return

        lock = self._settlement_locks.setdefault(contract_id, asyncio.Lock())
        async with lock:
            if contract_id in self._settled_contracts:
                return

            status = poc.get("status", "unknown")
            profit = poc.get("profit", 0)
            logger.info(
                f"CONTRATO ENCERRADO! ID: {contract_id} | "
                f"Resultado: {status} | Lucro: {profit}"
            )
            if on_settled_callback:
                await on_settled_callback(poc)

            # Só torna o settlement idempotente depois que a persistência
            # do chamador concluiu. Uma falha transitória continuará elegível
            # para o próximo update ou consulta pontual vendido da Deriv.
            self._settled_contracts.add(contract_id)
            await self._unsubscribe_contract(contract_id)
            self._expiry_events.pop(contract_id, None)
            self._expiry_times.pop(contract_id, None)

            current = asyncio.current_task()
            task = self._reconciliation_tasks.pop(contract_id, None)
            if task and task is not current:
                task.cancel()

    async def _reconcile_contract(
        self,
        contract_id,
        on_settled_callback,
        on_update_callback,
    ):
        try:
            await self._wait_until_reconciliation_due(contract_id)
            request = {
                "proposal_open_contract": 1,
                "contract_id": contract_id,
            }

            while (
                not self._closed
                and contract_id not in self._settled_contracts
            ):
                try:
                    response = await self.connection.send(request)
                    if isinstance(response, dict) and "error" not in response:
                        await self._handle_contract_payload(
                            contract_id,
                            response,
                            on_settled_callback,
                            on_update_callback,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"Falha ao reconciliar contrato {contract_id}: {exc}"
                    )

                if (
                    self._closed
                    or contract_id in self._settled_contracts
                ):
                    return

                due_at = self._expiry_times.get(contract_id)
                if due_at is not None:
                    due_at += self.expiry_grace_seconds
                delay = self.reconcile_interval_seconds
                if due_at is not None and due_at > time.time():
                    delay = due_at - time.time()
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._reconciliation_tasks.get(contract_id) is current:
                self._reconciliation_tasks.pop(contract_id, None)

    async def _wait_until_reconciliation_due(self, contract_id):
        expiry_event = self._expiry_events[contract_id]
        if contract_id not in self._expiry_times:
            try:
                await asyncio.wait_for(
                    expiry_event.wait(),
                    timeout=self.reconcile_interval_seconds,
                )
            except asyncio.TimeoutError:
                return

        expiry_time = self._expiry_times.get(contract_id)
        if expiry_time is None:
            return
        delay = expiry_time + self.expiry_grace_seconds - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

    async def _unsubscribe_contract(self, contract_id):
        subscription_key = f"contract:{contract_id}"
        if subscription_key not in self._subscription_keys:
            return
        self._subscription_keys.remove(subscription_key)
        await self.connection.unsubscribe(subscription_key)

    async def close(self):
        self._closed = True
        current = asyncio.current_task()
        tasks = list(self._reconciliation_tasks.values())
        for task in tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not current),
            return_exceptions=True,
        )

        contract_ids = [
            key.split(":", 1)[1]
            for key in list(self._subscription_keys)
        ]
        for contract_id in contract_ids:
            await self._unsubscribe_contract(contract_id)

        self._reconciliation_tasks.clear()
        self._expiry_events.clear()
        self._expiry_times.clear()
        self._settlement_locks.clear()
