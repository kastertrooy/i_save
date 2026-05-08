from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, update, func

from shared.database.connection import async_session
from shared.database.models import User, ServiceInstance, SystemSetting, ContentQueue
from shared.logger import get_logger
from admin_alerts import alert_queue_overflow, alert_service_down
from notifier import send_to_user

logger = get_logger('notification_service')


class NotificationScheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        self.scheduler.add_job(self._daily_subscription_reminders, CronTrigger(hour=9, minute=0))
        self.scheduler.add_job(self._hourly_subscription_check, CronTrigger(minute=0))
        self.scheduler.add_job(self._daily_reset_downloads, CronTrigger(hour=0, minute=0))
        self.scheduler.add_job(self._service_heartbeat_check, IntervalTrigger(minutes=2))
        self.scheduler.add_job(self._queue_overflow_check, IntervalTrigger(minutes=5))
        self.scheduler.start()
        logger.info('Notification scheduler started')

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info('Notification scheduler stopped')

    async def _daily_subscription_reminders(self) -> None:
        remind_days = await self._get_setting_value('subscription_remind_days', '3')
        try:
            days = int(remind_days)
        except ValueError:
            days = 3

        target_date = datetime.utcnow() + timedelta(days=days)
        start = datetime(target_date.year, target_date.month, target_date.day)
        end = start + timedelta(days=1)

        async with async_session() as session:
            stmt = (
                select(User)
                .where(User.subscription_until >= start)
                .where(User.subscription_until < end)
                .where(User.telegram_chat_id != None)
                .where(User.subscription_status.in_(['active', 'free_trial']))
            )
            result = await session.execute(stmt)
            users = result.scalars().all()

        for user in users:
            text = f"Напоминание: Ваша подписка истекает через {days} дня(дней)."
            await send_to_user(user.telegram_chat_id, text)
        logger.info('Sent subscription reminders to %s users', len(users))

    async def _hourly_subscription_check(self) -> None:
        now = datetime.utcnow()
        async with async_session() as session:
            stmt = (
                select(User)
                .where(User.subscription_until < now)
                .where(User.subscription_status != 'expired')
            )
            result = await session.execute(stmt)
            users = result.scalars().all()

            user_ids = [user.id for user in users]
            if user_ids:
                await session.execute(
                    update(User)
                    .where(User.id.in_(user_ids))
                    .values(subscription_status='expired')
                )
                await session.commit()

        for user in users:
            if user.telegram_chat_id:
                await send_to_user(user.telegram_chat_id, 'Ваша подписка истекла.')
        logger.info('Marked %s subscriptions as expired', len(users))

    async def _daily_reset_downloads(self) -> None:
        async with async_session() as session:
            await session.execute(update(User).values(daily_downloads_today=0))
            await session.commit()
        logger.info('Reset daily_downloads_today for all users')

    async def _service_heartbeat_check(self) -> None:
        threshold = datetime.utcnow() - timedelta(minutes=5)
        async with async_session() as session:
            stmt = (
                select(ServiceInstance)
                .where(ServiceInstance.last_heartbeat_at < threshold)
                .where(ServiceInstance.status == 'running')
            )
            result = await session.execute(stmt)
            services = result.scalars().all()

        for service in services:
            await alert_service_down(service.service_type)
        if services:
            logger.warning('Detected %s down services', len(services))

    async def _queue_overflow_check(self) -> None:
        threshold_value = await self._get_setting_value('queue_alert_threshold', '100')
        try:
            threshold = int(threshold_value)
        except ValueError:
            threshold = 100

        async with async_session() as session:
            stmt = select(func.count()).select_from(ContentQueue)
            result = await session.execute(stmt)
            queue_count = result.scalar_one() or 0

        if queue_count > threshold:
            await alert_queue_overflow(queue_count)
            logger.warning('Queue overflow detected: %s items', queue_count)
        else:
            logger.debug('Queue size %s is below threshold %s', queue_count, threshold)

    async def _get_setting_value(self, key: str, default: str) -> str:
        async with async_session() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == key)
            result = await session.execute(stmt)
            value = result.scalar_one_or_none()
        return value if value is not None else default
