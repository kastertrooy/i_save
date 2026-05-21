import asyncio
from datetime import date, datetime

from aiogram import Bot
from sqlalchemy import select, update
from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import ContentQueue, User
from shared.logger import get_logger
from .sender import send_to_user

logger = get_logger('media_sender')


class SenderWorker:
    def __init__(self) -> None:
        self.logger = logger

    async def run(self) -> None:
        self.logger.info('Media sender worker started')
        while True:
            try:
                items = await self._fetch_next_items()
                if not items:
                    await asyncio.sleep(10)
                    continue

                for item in items:
                    await self._process_item(item)

            except asyncio.CancelledError:
                self.logger.info('Sender worker cancelled')
                break
            except Exception as exc:
                self.logger.exception('Sender worker error: %s', exc)
                await asyncio.sleep(10)

    async def _fetch_next_items(self):
        async with async_session() as session:
            stmt = (
                select(ContentQueue)
                .where(ContentQueue.status == 'downloaded')
                .order_by(ContentQueue.id)
                .with_for_update(skip_locked=True)
                .limit(10)
            )
            result = await session.execute(stmt)
            items = result.scalars().all()

            if not items:
                return []

            item_ids = [item.id for item in items]
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id.in_(item_ids))
                .values(status='sending')
            )
            await session.commit()

        return items

    async def _process_item(self, item) -> None:
        self.logger.info('Processing content_queue id=%s', item.id)
        try:
            async with async_session() as session:
                stmt = select(User).where(User.instagram_id == item.instagram_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

            if not user or not user.telegram_chat_id:
                raise ValueError('User not available for delivery')

            if user.subscription_status == 'blocked':
                await self._delete_queue_item(item.id)
                self.logger.info('Deleted content_queue id=%s for blocked user %s', item.id, user.id)
                return

            user = await self._refresh_daily_counter(user)

            if user.subscription_status in ('expired', 'no_subscription') and (user.daily_downloads_today or 0) >= (user.daily_limit or 0):
                await self._notify_limit_reached(user.telegram_chat_id)
                await self._delete_queue_item(item.id)
                self.logger.info('Deleted content_queue id=%s because user %s reached daily limit', item.id, user.id)
                return

            await send_to_user(user, item)

            await self._mark_done(item.id)

            self.logger.info('Marked content_queue id=%s as done after delivery', item.id)

        except Exception as exc:
            self.logger.exception('Delivery error for content_queue id=%s: %s', item.id, exc)
            await self._handle_error(item)

    async def _handle_error(self, item) -> None:
        async with async_session() as session:
            retry_count = (item.retry_count or 0) + 1
            values = {'retry_count': retry_count}
            if retry_count >= 3:
                values['status'] = 'failed'
            else:
                values['status'] = 'downloaded'

            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item.id)
                .values(**values)
            )
            await session.commit()

    async def _delete_queue_item(self, item_id: int) -> None:
        async with async_session() as session:
            await session.execute(ContentQueue.__table__.delete().where(ContentQueue.id == item_id))
            await session.commit()

    async def _mark_done(self, item_id: int) -> None:
        async with async_session() as session:
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item_id)
                .values(status=ContentQueue.STATUS_DONE)
            )
            await session.commit()

    async def _refresh_daily_counter(self, user: User) -> User:
        today = date.today()
        if not user.daily_downloads_updated_at or user.daily_downloads_updated_at.date() != today:
            async with async_session() as session:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(daily_downloads_today=0, daily_downloads_updated_at=datetime.utcnow())
                )
                await session.commit()
            user.daily_downloads_today = 0
            user.daily_downloads_updated_at = datetime.utcnow()
        return user

    async def _notify_limit_reached(self, chat_id: int) -> None:
        bot = Bot(token=settings.telegram_bot_token)
        try:
            await bot.send_message(chat_id=chat_id, text='Лимит скачиваний на сегодня исчерпан.')
        finally:
            await bot.session.close()
