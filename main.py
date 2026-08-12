import asyncio
import signal

from core.orchestrator import BotOrchestrator
from core.event_publisher import HttpEventPublisher
from database.repository import DatabaseRepository
from nexus_trade.learning_lab import LearningLabService
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
    learning_publisher = HttpEventPublisher()
    await learning_publisher.start()
    learning_runner = asyncio.create_task(
        LearningLabService(repository.db_path).run_forever(
            shutdown, publisher=learning_publisher,
        ),
    )
    shutdown_waiter = asyncio.create_task(shutdown.wait())
    try:
        done, _ = await asyncio.wait(
            {runner, learning_runner, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if runner in done:
            await runner
        if learning_runner in done:
            await learning_runner
    finally:
        shutdown.set()
        shutdown_waiter.cancel()
        await orchestrator.stop()
        runner.cancel()
        learning_runner.cancel()
        await asyncio.gather(
            runner, learning_runner, shutdown_waiter, return_exceptions=True,
        )
        await learning_publisher.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("NexusTrader encerrado pelo operador")
