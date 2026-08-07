import asyncio
import time

import httpx

from config.settings import settings
from core.events import is_critical_event
from utils.logger import setup_logger

logger = setup_logger("EventPublisher")


class HttpEventPublisher:
    def __init__(
        self,
        base_url=None,
        internal_token=None,
        client=None,
        queue_max=None,
        retry_delays=(0.25, 1.0, 2.0),
    ):
        self.base_url = (base_url or settings.API_BASE_URL).rstrip("/")
        self.internal_token = internal_token if internal_token is not None else settings.INTERNAL_API_TOKEN
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
        self._queue = asyncio.Queue(maxsize=queue_max or settings.EVENT_QUEUE_MAX)
        self._worker_task = None
        self._closing = False
        self.dropped_market_events = 0
        self.failed_events = 0
        self.last_success_epoch = None
        self.last_failure_epoch = None
        self.retry_delays = tuple(float(delay) for delay in retry_delays)

    @property
    def queue_size(self):
        return self._queue.qsize()

    @property
    def is_healthy(self):
        worker_ok = self._worker_task is not None and not self._worker_task.done()
        recovered = (
            self.last_failure_epoch is None
            or (
                self.last_success_epoch is not None
                and self.last_success_epoch >= self.last_failure_epoch
            )
        )
        return worker_ok and recovered

    async def start(self):
        if self._worker_task is None or self._worker_task.done():
            self._closing = False
            self._worker_task = asyncio.create_task(self._worker())

    async def publish(self, event: dict) -> bool:
        if not isinstance(event, dict) or not event.get("type"):
            raise ValueError("evento invalido")
        try:
            self._queue.put_nowait(dict(event))
            return True
        except asyncio.QueueFull:
            if not is_critical_event(event):
                self.dropped_market_events += 1
                return False
            await self._queue.put(dict(event))
            return True

    async def _worker(self):
        while not self._closing:
            event = await self._queue.get()
            try:
                delays = self.retry_delays if is_critical_event(event) else ()
                for attempt in range(len(delays) + 1):
                    try:
                        response = await self._client.post(
                            f"{self.base_url}/api/v1/internal/events",
                            json=event,
                            headers={"X-Internal-Token": self.internal_token},
                        )
                        response.raise_for_status()
                        self.last_success_epoch = time.time()
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if attempt >= len(delays):
                            raise
                        logger.warning(
                            f"Falha transitória ao publicar {event.get('type')}; "
                            f"nova tentativa {attempt + 2}: {exc}"
                        )
                        await asyncio.sleep(delays[attempt])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failed_events += 1
                self.last_failure_epoch = time.time()
                logger.error(f"Falha ao publicar evento {event.get('type')}: {exc}")
            finally:
                self._queue.task_done()

    async def flush(self):
        await self._queue.join()

    async def close(self):
        if self._worker_task and not self._worker_task.done():
            await self.flush()
            self._closing = True
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
        await self._client.aclose()
