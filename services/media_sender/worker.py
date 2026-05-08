import asyncio
from sqlalchemy import select, update
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

            if user.subscription_status == 'expired' and user.daily_downloads_today >= user.daily_limit:
                raise ValueError('User daily limit reached')

            await send_to_user(user, item)

            async with async_session() as session:
                await session.execute(
                    ContentQueue.__table__.delete().where(ContentQueue.id == item.id)
                )
                await session.commit()

            self.logger.info('Deleted content_queue id=%s after delivery', item.id)

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
