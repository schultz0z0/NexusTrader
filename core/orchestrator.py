import asyncio
from dataclasses import dataclass

from utils.logger import setup_logger

logger = setup_logger("BotOrchestrator")


@dataclass
class RunningSession:
    session: object
    task: asyncio.Task
    config_revision: int
    stop_requested: bool = False


class BotOrchestrator:
    def __init__(self, repository, session_factory=None, reconcile_interval=1.0):
        self.repository = repository
        self.session_factory = session_factory or self._default_session_factory
        self.reconcile_interval = reconcile_interval
        self._sessions = {}
        self._stop_event = asyncio.Event()

    def _default_session_factory(self, bot):
        from core.bot_session import BotSession
        return BotSession(repository=self.repository, bot=bot)

    @property
    def running_bot_ids(self):
        return tuple(self._sessions.keys())

    async def reconcile_once(self):
        bots = {bot["id"]: bot for bot in await self.repository.list_bots()}
        await self._remove_finished_sessions()

        for bot_id, bot in bots.items():
            desired = bot.get("desired_state", "STOPPED")
            running = self._sessions.get(bot_id)
            if desired == "RUNNING":
                if running is None:
                    await self._start_session(bot)
                elif bot.get("config_revision", 1) != running.config_revision and not running.stop_requested:
                    running.stop_requested = True
                    await running.session.request_stop()
            elif running and not running.stop_requested:
                running.stop_requested = True
                await running.session.request_stop()

        for bot_id, running in list(self._sessions.items()):
            if bot_id not in bots and not running.stop_requested:
                running.stop_requested = True
                await running.session.request_stop()

    async def _start_session(self, bot):
        session = self.session_factory(dict(bot))
        task = asyncio.create_task(self._run_session(bot["id"], session))
        self._sessions[bot["id"]] = RunningSession(
            session=session,
            task=task,
            config_revision=int(bot.get("config_revision", 1)),
        )

    async def _run_session(self, bot_id, session):
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"Sessao {bot_id} encerrou com erro: {exc}")
            await self.repository.set_runtime_state(bot_id, "ERROR", str(exc))

    async def _remove_finished_sessions(self):
        for bot_id, running in list(self._sessions.items()):
            if running.task.done():
                await asyncio.gather(running.task, return_exceptions=True)
                self._sessions.pop(bot_id, None)

    async def run(self):
        self._stop_event.clear()
        while not self._stop_event.is_set():
            await self.reconcile_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.reconcile_interval)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stop_event.set()
        for running in self._sessions.values():
            if not running.stop_requested:
                running.stop_requested = True
                await running.session.request_stop()
        tasks = [running.task for running in self._sessions.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()
