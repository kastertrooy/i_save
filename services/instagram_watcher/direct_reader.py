import asyncio
import json
import random
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select, insert, update
from shared.database.connection import async_session
from shared.database.models import User, ContentQueue, SystemSetting
from shared.logger import get_logger
from .content_parser import parse_content_type, extract_urls, check_file_size
from .token_generator import generate_bind_token


class DirectReader:
    def __init__(self) -> None:
        self.logger = get_logger('instagram_watcher')
        self.browser_manager = None
        self.page = None
        self._seen_messages = set()

    async def start_listening(self, browser_manager) -> None:
        self.browser_manager = browser_manager
        self.page = browser_manager.page

        self.logger.info('Start listening to Instagram Direct')
        await self.page.goto('https://www.instagram.com/direct/inbox/', timeout=30000)
        await self.page.wait_for_selector('a[href*="/direct/t/"]', timeout=20000)

        self.page.on('response', lambda response: asyncio.create_task(self._handle_response(response)))
        self.page.on('websocket', self._handle_websocket)

    async def _handle_response(self, response) -> None:
        url = response.url
        if 'direct_v2' not in url and 'direct/' not in url:
            return

        try:
            data = await response.json()
        except Exception:
            return

        await self._parse_direct_response(data)

    def _handle_websocket(self, websocket) -> None:
        websocket.on('framereceived', lambda frame: asyncio.create_task(self._process_ws_frame(frame)))

    async def _process_ws_frame(self, frame) -> None:
        payload = frame.payload
        if not payload:
            return

        if isinstance(payload, bytes):
            try:
                payload = payload.decode('utf-8')
            except Exception:
                return

        try:
            data = json.loads(payload)
        except Exception:
            return

        await self._parse_direct_response(data)

    async def _parse_direct_response(self, data: dict) -> None:
        if not isinstance(data, dict):
            return

        candidates = []
        for key in ('threads', 'items', 'messages', 'pending_requests'):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)

        if not candidates and isinstance(data.get('message'), dict):
            candidates.append(data['message'])

        for item in candidates:
            if not isinstance(item, dict):
                continue
            message_id = item.get('item_id') or item.get('id')
            if not message_id or message_id in self._seen_messages:
                continue
            self._seen_messages.add(message_id)
            await self.on_new_message(item)

    async def on_new_message(self, message_data: dict) -> None:
        await asyncio.sleep(random.uniform(2.0, 8.0))

        instagram_id = (
            message_data.get('user_id')
            or message_data.get('sender_id')
            or message_data.get('owner_id')
            or message_data.get('from_id')
        )
        if not instagram_id:
            self.logger.warning('Unable to extract instagram_id from message_data')
            return

        content_type = parse_content_type(message_data)
        main_url, carousel_urls = extract_urls(message_data)
        if not main_url:
            self.logger.warning('No media URL found in message_data for %s', instagram_id)
            return

        max_size_setting = await self._get_system_setting('max_video_size_mb', '1024')
        try:
            max_size_mb = float(max_size_setting)
        except ValueError:
            max_size_mb = 1024.0

        if content_type == 'video':
            size_mb = check_file_size(main_url)
            if size_mb is not None and size_mb > max_size_mb:
                self.logger.warning('Video too large: %.2f MB > %s MB', size_mb, max_size_mb)
                return

        async with async_session() as session:
            stmt = select(User).where(User.instagram_id == instagram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await self._create_user(instagram_id, session)
                token = await generate_bind_token(instagram_id)
                self.logger.info('New user created and bind token generated for %s', instagram_id)
                await self.send_direct_message(instagram_id, f'Please bind your account: {token}')
                return

            if user.telegram_chat_id is None:
                token = await generate_bind_token(instagram_id)
                self.logger.info('Existing user without telegram, bind token generated for %s', instagram_id)
                await self.send_direct_message(instagram_id, f'Please bind your account: {token}')
                return

            content_values = {
                'instagram_id': instagram_id,
                'url': main_url,
                'content_type': content_type,
                'carousel_urls': carousel_urls or None,
                'status': 'pending',
                'retry_count': 0,
            }
            await session.execute(insert(ContentQueue).values(**content_values))
            await session.commit()
            self.logger.info('New content_queue item created for user %s', instagram_id)

    async def send_direct_message(self, instagram_id, text) -> None:
        if not self.page:
            self.logger.warning('No page available to send direct message')
            return

        try:
            thread_url = f'https://www.instagram.com/direct/t/{instagram_id}/'
            await self.page.goto(thread_url, timeout=30000)
            await self.page.wait_for_selector('textarea', timeout=15000)
            await self.page.fill('textarea', text)
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(random.uniform(2.0, 4.0))
        except Exception as exc:
            self.logger.warning('Failed to send direct message to %s: %s', instagram_id, exc)

    async def _create_user(self, instagram_id: str, session) -> None:
        default_limit = await self._get_system_setting('expired_daily_limit', '5')
        try:
            daily_limit = int(default_limit)
        except ValueError:
            daily_limit = 5

        stmt = insert(User).values(
            instagram_id=instagram_id,
            telegram_chat_id=None,
            language='ru',
            subscription_status='expired',
            daily_limit=daily_limit,
            bind_token=None,
            bind_token_expires_at=None,
            free_trial_used=False,
            subscription_until=None,
            telegram_username=None,
        )
        await session.execute(stmt)
        await session.commit()

    async def _get_system_setting(self, key: str, default: str) -> str:
        async with async_session() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == key)
            result = await session.execute(stmt)
            value = result.scalar_one_or_none()
        return value if value is not None else default
