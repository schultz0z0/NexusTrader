import asyncio

from core.orchestrator import BotOrchestrator
from database.repository import DatabaseRepository
from utils.logger import setup_logger

logger = setup_logger("NexusTraderMain")


async def main():
    logger.info("NexusTrader multi-bot orchestrator starting")
    repository = DatabaseRepository()
    await repository.init_db()
    orchestrator = BotOrchestrator(repository)
    try:
        await orchestrator.run()
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("NexusTrader encerrado pelo operador")
