from aiogram import Bot
from aiogram.types import InputFile, InputMediaPhoto, InputMediaVideo

from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import MediaCache, DeliveryLog, User
from shared.logger import get_logger

logger = get_logger('media_sender')


async def send_to_user(user: User, content_queue_item) -> None:
    """
    Отправляет медиа пользователю и сохраняет лог доставки.
    """
    bot = Bot(token=settings.telegram_bot_token)
    chat_id = user.telegram_chat_id
    if not chat_id:
        raise ValueError('User has no telegram_chat_id')

    async with async_session() as session:
        stmt = MediaCache.__table__.select().where(MediaCache.original_url == content_queue_item.url)
        result = await session.execute(stmt)
        cache_row = result.first()

    if not cache_row:
        raise ValueError('Media cache not found for url %s' % content_queue_item.url)

    cache = cache_row._mapping
    file_ids = {
        'video': cache.get('telegram_file_id_video'),
        'audio': cache.get('telegram_file_id_audio'),
        'photo': cache.get('telegram_file_id_photo'),
    }

    if not any(file_ids.values()):
        raise ValueError('No file IDs available in cache for url %s' % content_queue_item.url)

    delivery_type = content_queue_item.content_type
    status = 'success'

    try:
        if delivery_type in ('video', 'reel', 'story'):
            if file_ids['video']:
                await bot.send_video(chat_id=chat_id, video=file_ids['video'])
            if file_ids['audio']:
                await bot.send_audio(chat_id=chat_id, audio=file_ids['audio'])
        elif delivery_type == 'photo':
            if not file_ids['photo']:
                raise ValueError('Photo file_id missing')
            await bot.send_photo(chat_id=chat_id, photo=file_ids['photo'])
        elif delivery_type == 'carousel':
            media = []
            if file_ids['photo']:
                media.append(InputMediaPhoto(media=file_ids['photo']))
            if file_ids['video']:
                media.append(InputMediaVideo(media=file_ids['video']))
            if not media:
                raise ValueError('Carousel media IDs missing')
            await bot.send_media_group(chat_id=chat_id, media=media)
        else:
            if file_ids['video']:
                await bot.send_video(chat_id=chat_id, video=file_ids['video'])
            elif file_ids['photo']:
                await bot.send_photo(chat_id=chat_id, photo=file_ids['photo'])
            else:
                raise ValueError('Unsupported content type %s' % delivery_type)

        async with async_session() as session:
            await session.execute(
                DeliveryLog.__table__.insert().values(
                    content_queue_id=content_queue_item.id,
                    user_id=user.id,
                    delivery_type=delivery_type,
                    status=status,
                )
            )
            await session.execute(
                User.__table__.update()
                .where(User.id == user.id)
                .values(daily_downloads_today=User.daily_downloads_today + 1)
            )
            await session.commit()

    except Exception as exc:
        status = 'failed'
        async with async_session() as session:
            await session.execute(
                DeliveryLog.__table__.insert().values(
                    content_queue_id=content_queue_item.id,
                    user_id=user.id,
                    delivery_type=delivery_type,
                    status=status,
                )
            )
            await session.commit()
        logger.exception('Failed to deliver content_queue id=%s: %s', content_queue_item.id, exc)
        raise
    finally:
        await bot.session.close()
