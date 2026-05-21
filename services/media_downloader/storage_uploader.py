import os
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import MediaCache, SystemSetting, TelegramStorageGroup
from shared.logger import get_logger

logger = get_logger('media_downloader')


async def _get_storage_group_id() -> int:
    storage_group_id = os.getenv('STORAGE_GROUP_ID')
    if storage_group_id:
        return await _resolve_storage_chat_id(storage_group_id)

    async with async_session() as session:
        stmt = SystemSetting.__table__.select().where(SystemSetting.key == 'telegram_storage_group_id')
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if row is None:
        raise RuntimeError('No telegram_storage_group_id found in system_settings')

    try:
        return await _resolve_storage_chat_id(row)
    except (TypeError, ValueError):
        raise RuntimeError('telegram_storage_group_id must be an integer')


async def _resolve_storage_chat_id(value: str | int) -> int:
    storage_id = int(value)
    async with async_session() as session:
        stmt = SystemSetting.__table__.select().where(SystemSetting.key == 'telegram_storage_group_chat_id')
        result = await session.execute(stmt)
        configured_chat_id = result.scalar_one_or_none()
        if configured_chat_id:
            return int(configured_chat_id)

        stmt = TelegramStorageGroup.__table__.select().where(TelegramStorageGroup.id == storage_id)
        result = await session.execute(stmt)
        row = result.first()
        if row:
            return int(row._mapping['telegram_group_id'])

    return storage_id


async def upload_to_storage(file_path: str, content_type: str, queue_id: int) -> dict:
    """
    Отправляет файл в Telegram группу-хранилище и возвращает идентификаторы file_id.
    """
    bot = Bot(token=settings.telegram_bot_token)
    storage_group_id = await _get_storage_group_id()
    file = FSInputFile(file_path)
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

    try:
        if file_type == 'photo':
            message = await bot.send_photo(chat_id=storage_group_id, photo=file)
            result['file_id_photo'] = message.photo[-1].file_id
        elif file_type == 'audio':
            message = await bot.send_audio(chat_id=storage_group_id, audio=file)
            result['file_id_audio'] = message.audio.file_id
        else:
            message = await bot.send_video(chat_id=storage_group_id, video=file)
            if not message.video:
                raise RuntimeError('Telegram did not return video file_id for uploaded media')
            result['file_id_video'] = message.video.file_id
            document_message = await bot.send_document(
                chat_id=storage_group_id,
                document=FSInputFile(file_path),
            )
            if not document_message.document:
                raise RuntimeError('Telegram did not return document file_id for uploaded media')
            result['file_id_document'] = document_message.document.file_id

        return result
    finally:
        await bot.session.close()


async def save_to_cache(url: str, file_ids: dict, storage_group_id: int, size_mb: float) -> None:
    values = {
        'original_url': url,
        'telegram_file_id_video': file_ids.get('file_id_video'),
        'telegram_file_id_audio': file_ids.get('file_id_audio'),
        'telegram_file_id_photo': file_ids.get('file_id_photo'),
        'telegram_file_id_document': file_ids.get('file_id_document'),
        'size_mb': size_mb,
        'created_at': datetime.utcnow(),
        'storage_group_id': storage_group_id,
    }
    if file_ids.get('file_ids') is not None:
        values['telegram_file_ids'] = file_ids['file_ids']

    async with async_session() as session:
        await session.execute(MediaCache.__table__.insert().values(**values))
        await session.commit()
    logger.info('Saved media cache for %s (size %.2f MB)', url, size_mb)
