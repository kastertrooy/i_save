import asyncio
import signal

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from shared.config import settings
from shared.database.connection import init_db
from shared.logger import get_logger

from services.telegram_bot.handlers.start import router as start_router
from services.telegram_bot.handlers.status import router as status_router
from services.telegram_bot.handlers.help import router as help_router
from services.telegram_bot.handlers.language import router as language_router

logger = get_logger('telegram_bot')


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(start_router)
    dp.include_router(status_router)
    dp.include_router(help_router)
    dp.include_router(language_router)


async def main() -> None:
    logger.info('Starting telegram_bot...')
    await init_db()

    bot = Bot(token=settings.telegram_bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    register_handlers(dp)

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info('Shutdown signal received, stopping telegram_bot...')
    finally:
        await bot.session.close()
        await storage.close()
        logger.info('telegram_bot stopped.')


if __name__ == '__main__':
    asyncio.run(main())