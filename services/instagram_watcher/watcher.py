import asyncio
from datetime import date, datetime

from sqlalchemy import select, insert, update
from shared.database.connection import async_session
from shared.database.models import (
    User,
    InstagramAccount,
    ContentQueue,
    SystemSetting,
)
from shared.logger import get_logger
from shared.service_heartbeat import get_instance_name, update_service_heartbeat
from .direct_reader import DirectReader
from .token_generator import generate_bind_token, get_bind_link


class InstagramWatcher:
    def __init__(self, browser_manager) -> None:
        self.browser_manager = browser_manager
        self.direct_reader = DirectReader()
        self.account_id = None
        self.health_task = None
        self.logger = get_logger('instagram_watcher')
        self.instance_name = get_instance_name('instagram_watcher_main')

    async def start(self, account_id: int) -> None:
        self.account_id = account_id
        async with async_session() as session:
            stmt = select(InstagramAccount).where(InstagramAccount.id == account_id)
            result = await session.execute(stmt)
            account = result.scalar_one_or_none()

        if not account:
            raise ValueError(f'Instagram account {account_id} not found')

        await self.browser_manager.start(account_id)
        await self.direct_reader.start_listening(self.browser_manager)
        await self._update_service_instance(status='running')
        self.health_task = asyncio.create_task(self._heartbeat_loop())
        self.logger.info('InstagramWatcher started for account %s', account_id)

    async def stop(self) -> None:
        if self.health_task:
            self.health_task.cancel()
            try:
                await self.health_task
            except asyncio.CancelledError:
                pass

        if self.direct_reader.page:
            try:
                await self.direct_reader.stop()
                await self.direct_reader.page.close()
            except Exception as exc:
                self.logger.warning('Error closing direct reader page: %s', exc)

        await self._update_service_instance(status='stopped')
        self.logger.info('InstagramWatcher stopped')

    async def handle_user(self, instagram_id: str, message_data: dict) -> None:
        instagram_username = self._extract_instagram_username(message_data, instagram_id)
        async with async_session() as session:
            stmt = select(User).where(User.instagram_id == instagram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

        if not user:
            await self._create_user(instagram_id, instagram_username)
            token = await generate_bind_token(instagram_id)
            await self.direct_reader.send_direct_message(
                message_data,
                f'Please bind your account: {get_bind_link(token)}',
            )
            await self._insert_content_queue(instagram_id, message_data, status='no_telegram')
            return

        if instagram_username and user.instagram_username != instagram_username:
            async with async_session() as session:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(instagram_username=instagram_username)
                )
                await session.commit()
            user.instagram_username = instagram_username

        if user.telegram_chat_id is None:
            token = await generate_bind_token(instagram_id)
            await self.direct_reader.send_direct_message(
                message_data,
                f'Please bind your account: {get_bind_link(token)}',
            )
            await self._insert_content_queue(instagram_id, message_data, status='no_telegram')
            return

        subscription_status = user.subscription_status
        if subscription_status in ('active', 'free_trial'):
            await self._insert_content_queue(instagram_id, message_data, status='pending')
            return

        if subscription_status == 'blocked':
            return

        if subscription_status in ('expired', 'no_subscription'):
            user = await self._refresh_daily_counter(user)
            if user.daily_downloads_today >= (user.daily_limit or 0):
                await self.direct_reader.send_direct_message(
                    message_data,
                    'Your daily download limit has been reached.',
                )
                return
            await self._insert_content_queue(instagram_id, message_data, status='pending')
            return

        await self._insert_content_queue(instagram_id, message_data, status='pending')

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                await self._update_service_instance(status='running')
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.exception('Heartbeat loop error: %s', exc)

    async def _update_service_instance(self, status: str) -> None:
        await update_service_heartbeat('instagram_watcher', self.instance_name, status=status)

    def _extract_instagram_username(self, message_data: dict, instagram_id: str | None) -> str | None:
        username = message_data.get('username')
        if isinstance(username, str) and username:
            return username

        users = message_data.get('users')
        if isinstance(users, list):
            for user in users:
                if not isinstance(user, dict):
                    continue
                user_id = user.get('pk') or user.get('id') or user.get('user_id')
                username = user.get('username')
                if username and (not instagram_id or str(user_id) == str(instagram_id)):
                    return username

        return None

    async def _create_user(self, instagram_id: str, instagram_username: str | None = None) -> None:
        default_limit = await self._get_system_setting('expired_daily_limit', '5')
        try:
            daily_limit = int(default_limit)
        except ValueError:
            daily_limit = 5

        async with async_session() as session:
            stmt = insert(User).values(
                instagram_id=instagram_id,
                instagram_username=instagram_username,
                telegram_chat_id=None,
                language='ru',
                subscription_status='expired',
                daily_limit=daily_limit,
                bind_token=None,
                bind_token_expires_at=None,
                free_trial_used=False,
                subscription_until=None,
                telegram_username=None,
                daily_downloads_today=0,
                daily_downloads_updated_at=None,
            )
            await session.execute(stmt)
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

    async def _insert_content_queue(self, instagram_id: str, message_data: dict, status: str) -> None:
        content_type = self._get_content_type(message_data)
        main_url, carousel_urls = self._get_urls(message_data)
        async with async_session() as session:
            await session.execute(
                insert(ContentQueue).values(
                    instagram_id=instagram_id,
                    url=main_url,
                    content_type=content_type,
                    carousel_urls=carousel_urls or None,
                    status=status,
                    retry_count=0,
                )
            )
            await session.commit()

    async def _get_system_setting(self, key: str, default: str) -> str:
        async with async_session() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == key)
            result = await session.execute(stmt)
            value = result.scalar_one_or_none()
        return value if value is not None else default

    def _get_content_type(self, message_data: dict) -> str:
        from .content_parser import parse_content_type

        return parse_content_type(message_data)

    def _get_urls(self, message_data: dict) -> tuple[str, list]:
        from .content_parser import extract_urls

        return extract_urls(message_data)
