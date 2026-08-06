import asyncio
import signal

from core.orchestrator import BotOrchestrator
from database.repository import DatabaseRepository
from utils.logger import setup_logger

logger = setup_logger("NexusTraderMain")


async def main():
    logger.info("NexusTrader multi-bot orchestrator starting")
    repository = DatabaseRepository()
    await repository.init_db()
    orchestrator = BotOrchestrator(repository)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, name, None)
        if signum is not None:
            try:
                loop.add_signal_handler(signum, shutdown.set)
            except (NotImplementedError, RuntimeError):
                pass

    runner = asyncio.create_task(orchestrator.run())
    shutdown_waiter = asyncio.create_task(shutdown.wait())
    try:
        done, _ = await asyncio.wait(
            {runner, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if runner in done:
            await runner
    finally:
        shutdown_waiter.cancel()
        await orchestrator.stop()
        runner.cancel()
        await asyncio.gather(runner, shutdown_waiter, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("NexusTrader encerrado pelo operador")
