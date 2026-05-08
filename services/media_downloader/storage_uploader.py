from pathlib import Path

from aiogram import Bot
from aiogram.types import InputFile

from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import MediaCache, SystemSetting
from shared.logger import get_logger

logger = get_logger('media_downloader')


async def _get_storage_group_id() -> int:
    async with async_session() as session:
        stmt = SystemSetting.__table__.select().where(SystemSetting.key == 'telegram_storage_group_id')
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if row is None:
        raise RuntimeError('No telegram_storage_group_id found in system_settings')

    try:
        return int(row)
    except (TypeError, ValueError):
        raise RuntimeError('telegram_storage_group_id must be an integer')


async def upload_to_storage(file_path: str, content_type: str, queue_id: int) -> dict:
    """
    Отправляет файл в Telegram группу-хранилище и возвращает идентификаторы file_id.
    """
    bot = Bot(token=settings.telegram_bot_token)
    storage_group_id = await _get_storage_group_id()
    file = InputFile(file_path)
    result = {}
    file_type = content_type

    if file_type == 'carousel':
        suffix = Path(file_path).suffix.lower()
        if suffix in ('.jpg', '.jpeg', '.png', '.webp'):
            file_type = 'photo'
        elif suffix in ('.mp3', '.wav', '.ogg', '.aac'):
            file_type = 'audio'
        else:
            file_type = 'video'

    logger.info('Uploading %s to storage group %s', file_path, storage_group_id)

    if file_type == 'photo':
        message = await bot.send_photo(chat_id=storage_group_id, photo=file)
        result['file_id_photo'] = message.photo[-1].file_id
    elif file_type == 'audio':
        message = await bot.send_audio(chat_id=storage_group_id, audio=file)
        result['file_id_audio'] = message.audio.file_id
    else:
        message = await bot.send_video(chat_id=storage_group_id, video=file)
        result['file_id_video'] = message.video.file_id

    await bot.session.close()
    return result


async def save_to_cache(url: str, file_ids: dict, storage_group_id: int, size_mb: float) -> None:
    async with async_session() as session:
        await session.execute(
            MediaCache.__table__.insert().values(
                original_url=url,
                telegram_file_id_video=file_ids.get('file_id_video'),
                telegram_file_id_audio=file_ids.get('file_id_audio'),
                telegram_file_id_photo=file_ids.get('file_id_photo'),
                storage_group_id=storage_group_id,
            )
        )
        await session.commit()
    logger.info('Saved media cache for %s (size %.2f MB)', url, size_mb)
