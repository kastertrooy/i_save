import asyncio
import signal

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from shared.config import settings
from shared.database.connection import init_db
from shared.logger import get_logger
from shared.service_heartbeat import get_instance_name, start_heartbeat_task, stop_heartbeat_task

from services.telegram_bot.handlers.start import router as start_router
from services.telegram_bot.handlers.status import router as status_router
from services.telegram_bot.handlers.help import router as help_router
from services.telegram_bot.handlers.language import router as language_router
from services.telegram_bot.middleware.db import DatabaseSessionMiddleware

logger = get_logger('telegram_bot')
SERVICE_TYPE = 'telegram_bot'
INSTANCE_NAME = get_instance_name('telegram_bot_main')


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
    db_middleware = DatabaseSessionMiddleware()
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)
    register_handlers(dp)
    heartbeat_task = start_heartbeat_task(SERVICE_TYPE, INSTANCE_NAME)

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info('Shutdown signal received, stopping telegram_bot...')
    finally:
        await bot.session.close()
        await storage.close()
        await stop_heartbeat_task(heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)
        logger.info('telegram_bot stopped.')


if __name__ == '__main__':
    asyncio.run(main())
