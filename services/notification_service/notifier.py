import asyncio
from aiogram import Bot

from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import User, SystemSetting
from shared.logger import get_logger

logger = get_logger('notification_service')


async def _get_admin_chat_id() -> int:
    async with async_session() as session:
        stmt = SystemSetting.__table__.select().where(SystemSetting.key == 'admin_telegram_chat_id')
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if row is not None:
        try:
            return int(row)
        except (TypeError, ValueError):
            logger.warning('Invalid admin_telegram_chat_id in system_settings: %s', row)

    return settings.admin_telegram_chat_id


async def _get_bot() -> Bot:
    bot = Bot(token=settings.telegram_bot_token)
    return bot


async def send_to_user(telegram_chat_id: int, text: str) -> None:
    bot = await _get_bot()
    try:
        await bot.send_message(chat_id=telegram_chat_id, text=text)
        logger.info('Sent notification to user %s', telegram_chat_id)
    except Exception as exc:
        logger.exception('Failed to send notification to user %s: %s', telegram_chat_id, exc)
        raise
    finally:
        await asyncio.sleep(0.05)
        await bot.session.close()


async def send_to_admin(text: str) -> None:
    chat_id = await _get_admin_chat_id()
    bot = await _get_bot()
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        logger.info('Sent admin notification to %s', chat_id)
    except Exception as exc:
        logger.exception('Failed to send admin notification: %s', exc)
        raise
    finally:
        await asyncio.sleep(0.05)
        await bot.session.close()


async def notify_all_users(text: str) -> None:
    async with async_session() as session:
        stmt = User.__table__.select().where(User.telegram_chat_id != None)
        result = await session.execute(stmt)
        users = result.fetchall()

    bot = await _get_bot()
    try:
        for row in users:
            telegram_chat_id = row['telegram_chat_id'] if isinstance(row, dict) else row[User.__table__.c.telegram_chat_id]
            if telegram_chat_id:
                try:
                    await bot.send_message(chat_id=int(telegram_chat_id), text=text)
                    logger.info('Sent notification to user %s', telegram_chat_id)
                except Exception as exc:
                    logger.warning('Failed to notify user %s: %s', telegram_chat_id, exc)
                await asyncio.sleep(0.05)
    finally:
        await bot.session.close()