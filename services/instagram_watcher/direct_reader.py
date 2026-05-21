import asyncio
from datetime import date, datetime, timedelta
import json
import os
import random
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select, insert, update
from shared.database.connection import async_session
from shared.database.models import User, ContentQueue, DirectMessageLog, SystemSetting
from shared.logger import get_logger
from .content_parser import parse_content_type, extract_urls, check_file_size
from .token_generator import generate_bind_token, get_bind_link


class DirectReader:
    def __init__(self) -> None:
        self.logger = get_logger('instagram_watcher')
        self.browser_manager = None
        self.page = None
        self._seen_messages = set()
        self._suppress_direct_responses = False
        self._poll_task = None
        self._disabled_direct_endpoints = set()
        self._page_lock = asyncio.Lock()

    async def start_listening(self, browser_manager) -> None:
        self.browser_manager = browser_manager
        self.page = browser_manager.page

        self.logger.info('Start listening to Instagram Direct')
        self.page.on('response', lambda response: asyncio.create_task(self._handle_response(response)))
        self.page.on('websocket', self._handle_websocket)

        await self.page.goto('https://www.instagram.com/direct/inbox/', timeout=30000)
        await self.page.wait_for_load_state('domcontentloaded', timeout=20000)
        await asyncio.sleep(random.uniform(2.0, 8.0))

        if '/accounts/login' in self.page.url or '/login/' in self.page.url:
            raise RuntimeError('Instagram Direct redirected to login; cookies are expired')

        await self._accept_visible_message_requests()
        await self._load_initial_inbox_snapshot()
        self._poll_task = asyncio.create_task(self._poll_inbox_loop())

        thread_count = await self.page.locator('a[href*="/direct/t/"]').count()
        self.logger.info(
            'Instagram Direct listener is active url=%s visible_threads=%s',
            self.page.url,
            thread_count,
        )

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _poll_inbox_loop(self) -> None:
        try:
            interval = int(os.getenv('DIRECT_POLL_INTERVAL_SECONDS', '20'))
        except ValueError:
            interval = 20
        interval = max(interval, 5)

        while True:
            try:
                await asyncio.sleep(interval)
                await self._accept_visible_message_requests()
                snapshots = await self._fetch_direct_snapshots()
                for data in snapshots:
                    await self._parse_direct_response(data)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.warning('Direct inbox polling failed: %s', exc)

    async def _fetch_inbox_snapshot(self) -> dict | None:
        snapshots = await self._fetch_direct_snapshots()
        return snapshots[0] if snapshots else None

    async def _fetch_direct_snapshots(self) -> list[dict]:
        if not self.page:
            return []

        endpoints = (
            '/api/v1/direct_v2/inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&use_unified_inbox=true&limit=50',
            '/api/v1/direct_v2/pending_inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&limit=20',
            '/api/v1/direct_share/pending/',
        )
        active_endpoints = [endpoint for endpoint in endpoints if endpoint not in self._disabled_direct_endpoints]

        snapshots = []
        try:
            self._suppress_direct_responses = True
            results = await self.page.evaluate(
                """async (endpoints) => {
                    const output = [];
                    for (const endpoint of endpoints) {
                        try {
                            const response = await fetch(endpoint, {
                                credentials: 'include',
                                headers: { 'X-IG-App-ID': '936619743392459' }
                            });
                            if (!response.ok) {
                                output.push({ endpoint, error: `HTTP ${response.status}` });
                                continue;
                            }
                            const data = await response.json();
                            output.push({ endpoint, data });
                        } catch (error) {
                            output.push({ endpoint, error: String(error) });
                        }
                    }
                    return output;
                }""",
                active_endpoints,
            )
        finally:
            self._suppress_direct_responses = False

        if not isinstance(results, list):
            return []

        for result in results:
            if not isinstance(result, dict):
                continue
            endpoint = result.get('endpoint')
            if result.get('error'):
                self.logger.warning('Direct polling request failed endpoint=%s error=%s', endpoint, result['error'])
                if result['error'] == 'HTTP 404':
                    self._disabled_direct_endpoints.add(endpoint)
                    self.logger.info('Disabled unsupported Direct polling endpoint=%s', endpoint)
                continue
            data = result.get('data')
            if isinstance(data, dict):
                if endpoint and 'pending' in endpoint:
                    data = dict(data)
                    data['_is_pending_inbox_snapshot'] = True
                candidate_count = len(self._collect_message_candidates(data))
                self.logger.debug('Direct polling snapshot endpoint=%s candidates=%s', endpoint, candidate_count)
                snapshots.append(data)

        return snapshots

    async def _load_initial_inbox_snapshot(self) -> None:
        try:
            snapshots = await self._fetch_direct_snapshots()
        except Exception as exc:
            self.logger.warning('Failed to load initial Direct inbox snapshot: %s', exc)
            return

        if not snapshots:
            return

        candidates = []
        for data in snapshots:
            candidates.extend(self._collect_message_candidates(data))
        processed_count = 0
        for item in candidates:
            if not isinstance(item, dict):
                continue
            message_id = item.get('item_id') or item.get('id')
            if not message_id or message_id in self._seen_messages:
                continue
            self._seen_messages.add(message_id)
            if item.get('is_sent_by_viewer'):
                continue
            if item.get('_is_pending_request') or self._should_process_initial_message(item):
                if await self._has_processed_message(message_id):
                    continue
                await self.on_new_message(item)
                processed_count += 1
                continue
            if await self._has_any_message_log(message_id):
                continue
            instagram_id = (
                item.get('user_id')
                or item.get('sender_id')
                or item.get('owner_id')
                or item.get('from_id')
            )
            content_type = parse_content_type(item)
            main_url, _ = extract_urls(item)
            await self._record_direct_message(
                item,
                instagram_id=instagram_id,
                url=main_url,
                content_type=content_type,
                status='snapshot',
                error='Initial inbox snapshot; not processed as a new message',
            )
        self.logger.info(
            'Initial Direct inbox snapshot recorded %s messages and processed %s incoming messages',
            len(candidates),
            processed_count,
        )

    async def _handle_response(self, response) -> None:
        url = response.url
        if 'direct_v2' not in url and 'direct/' not in url:
            return
        if self._suppress_direct_responses:
            return

        self.logger.debug('Direct response received: %s', url)
        try:
            data = await response.json()
        except Exception:
            return

        await self._parse_direct_response(data)

    def _handle_websocket(self, websocket) -> None:
        self.logger.info('Instagram websocket opened: %s', websocket.url)
        websocket.on('framereceived', lambda frame: asyncio.create_task(self._process_ws_frame(frame)))

    async def _process_ws_frame(self, frame) -> None:
        payload = getattr(frame, 'payload', frame)
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

        candidates = self._collect_message_candidates(data)

        self.logger.debug('Direct parser collected %s candidates', len(candidates))

        for item in candidates:
            if not isinstance(item, dict):
                continue
            message_id = item.get('item_id') or item.get('id')
            if not message_id or message_id in self._seen_messages:
                continue
            if item.get('is_sent_by_viewer') or await self._has_processed_message(message_id):
                continue
            self._seen_messages.add(message_id)
            self.logger.info('New Instagram Direct message candidate received: %s', message_id)
            await self.on_new_message(item)

    def _collect_message_candidates(self, data: dict) -> list[dict]:
        candidates = []

        def add_item(
            value,
            thread_id: str | None = None,
            usernames: dict[str, str] | None = None,
            is_pending_request: bool = False,
        ) -> None:
            if not isinstance(value, dict):
                return
            item_type = value.get('item_type') or value.get('type')
            if value.get('item_id') or value.get('id') or item_type in ('text', 'link', 'media_share', 'clip', 'raven_media'):
                if thread_id and not value.get('thread_id') and not value.get('thread_v2_id'):
                    value = dict(value)
                    value['thread_id'] = thread_id
                sender_id = (
                    value.get('user_id')
                    or value.get('sender_id')
                    or value.get('owner_id')
                    or value.get('from_id')
                )
                if usernames and sender_id and not value.get('username'):
                    username = usernames.get(str(sender_id))
                    if username:
                        value = dict(value)
                        value['username'] = username
                if is_pending_request and not value.get('_is_pending_request'):
                    value = dict(value)
                    value['_is_pending_request'] = True
                candidates.append(value)

        def extract_usernames(value) -> dict[str, str]:
            if not isinstance(value, dict):
                return {}
            result = {}
            users = value.get('users')
            if not isinstance(users, list):
                return result
            for user in users:
                if not isinstance(user, dict):
                    continue
                user_id = user.get('pk') or user.get('id') or user.get('user_id')
                username = user.get('username')
                if user_id and username:
                    result[str(user_id)] = username
            return result

        def walk(
            value,
            current_thread_id: str | None = None,
            current_usernames: dict[str, str] | None = None,
            current_is_pending_request: bool = False,
        ) -> None:
            if isinstance(value, list):
                for entry in value:
                    walk(entry, current_thread_id, current_usernames, current_is_pending_request)
                return
            if not isinstance(value, dict):
                return

            is_pending_request = current_is_pending_request or bool(
                value.get('is_pending')
                or value.get('is_pending_request')
                or value.get('_is_pending_inbox_snapshot')
                or value.get('pending')
                or value.get('folder') == 'pending'
            )
            thread_id = (
                value.get('thread_id')
                or value.get('thread_v2_id')
                or current_thread_id
            )
            usernames = dict(current_usernames or {})
            usernames.update(extract_usernames(value))
            add_item(value, thread_id, usernames, is_pending_request)
            for key in ('threads', 'items', 'messages', 'pending_requests', 'thread', 'inbox', 'payload'):
                nested = value.get(key)
                if isinstance(nested, (dict, list)):
                    walk(nested, thread_id, usernames, is_pending_request or key == 'pending_requests')

        walk(data)
        return candidates

    def _should_process_initial_message(self, message_data: dict) -> bool:
        try:
            window_minutes = int(os.getenv('INITIAL_INBOX_PROCESS_WINDOW_MINUTES', '120'))
        except ValueError:
            window_minutes = 120

        if window_minutes <= 0:
            return False

        timestamp = message_data.get('timestamp')
        if not timestamp:
            return False

        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            return False

        if timestamp_value > 100_000_000_000_000:
            message_seconds = timestamp_value / 1_000_000
        elif timestamp_value > 100_000_000_000:
            message_seconds = timestamp_value / 1_000
        else:
            message_seconds = timestamp_value

        age_seconds = datetime.utcnow().timestamp() - message_seconds
        return 0 <= age_seconds <= window_minutes * 60

    async def on_new_message(self, message_data: dict) -> None:
        await asyncio.sleep(random.uniform(2.0, 8.0))

        if message_data.get('is_sent_by_viewer'):
            self.logger.debug('Ignored own Instagram Direct message')
            return

        instagram_id = (
            message_data.get('user_id')
            or message_data.get('sender_id')
            or message_data.get('owner_id')
            or message_data.get('from_id')
        )
        instagram_username = self._extract_instagram_username(message_data, instagram_id)
        content_type = parse_content_type(message_data)
        main_url, carousel_urls = extract_urls(message_data)
        message_log_id = await self._record_direct_message(
            message_data,
            instagram_id=instagram_id,
            url=main_url,
            content_type=content_type,
            status='received',
        )

        if not instagram_id:
            self.logger.warning('Unable to extract instagram_id from message_data')
            await self._update_direct_message_status(
                message_log_id,
                'ignored',
                'Unable to extract instagram_id from message_data',
            )
            return

        if message_data.get('_is_pending_request'):
            accepted, accept_error = await self._accept_message_request(message_data)
            if not accepted:
                self.logger.warning('Could not accept Instagram message request: %s', accept_error)
                await self._update_direct_message_status(
                    message_log_id,
                    'request_accept_failed',
                    accept_error,
                )

        async with async_session() as session:
            stmt = select(User).where(User.instagram_id == instagram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await self._create_user(instagram_id, instagram_username, session)
                token = await generate_bind_token(instagram_id)
                self.logger.info('New user created and bind token generated for %s', instagram_id)
                if main_url:
                    await session.execute(insert(ContentQueue).values(
                        instagram_id=instagram_id,
                        url=main_url,
                        content_type=content_type,
                        carousel_urls=carousel_urls or None,
                        status='no_telegram',
                        retry_count=0,
                    ))
                await session.commit()
                sent, error = await self.send_direct_message(
                    message_data,
                    f'Please bind your Telegram account: {get_bind_link(token)}',
                )
                await self._update_direct_message_status(
                    message_log_id,
                    'needs_telegram' if sent else 'needs_telegram_message_failed',
                    error,
                )
                return

            if instagram_username and user.instagram_username != instagram_username:
                user.instagram_username = instagram_username

            if user.telegram_chat_id is None:
                token = user.bind_token
                if not token or not user.bind_token_expires_at or user.bind_token_expires_at <= datetime.utcnow():
                    token = await generate_bind_token(instagram_id)
                    self.logger.info('Existing user without telegram, bind token generated for %s', instagram_id)
                if main_url:
                    await session.execute(insert(ContentQueue).values(
                        instagram_id=instagram_id,
                        url=main_url,
                        content_type=content_type,
                        carousel_urls=carousel_urls or None,
                        status='no_telegram',
                        retry_count=0,
                    ))
                await session.commit()
                if main_url and await self._has_recent_bind_prompt(instagram_id):
                    sent, error = True, None
                else:
                    sent, error = await self.send_direct_message(
                        message_data,
                        f'Please bind your Telegram account: {get_bind_link(token)}',
                    )
                await self._update_direct_message_status(
                    message_log_id,
                    'needs_telegram' if sent else 'needs_telegram_message_failed',
                    error,
                )
                return

            if instagram_username:
                await session.commit()

        if not main_url:
            self.logger.warning('No media URL found in message_data for %s', instagram_id)
            await self._update_direct_message_status(
                message_log_id,
                'ignored',
                'No media URL found in message_data',
            )
            return

        if not await self._passes_following_check(message_data, message_log_id):
            await self.send_direct_message(
                message_data,
                'This Instagram account is not available for download. Please send content from an account this service follows.',
            )
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
                await self._update_direct_message_status(
                    message_log_id,
                    'ignored',
                    f'Video too large: {size_mb:.2f} MB > {max_size_mb} MB',
                )
                return

        if user.subscription_status == 'blocked':
            await self._update_direct_message_status(message_log_id, 'ignored', 'User subscription is blocked')
            return

        if user.subscription_status in ('expired', 'no_subscription'):
            user = await self._refresh_daily_counter(user)
            if (user.daily_downloads_today or 0) >= (user.daily_limit or 0):
                await self.send_direct_message(message_data, 'Your daily download limit has been reached.')
                await self._update_direct_message_status(message_log_id, 'limit_reached')
                return

        async with async_session() as session:
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
            await self._update_direct_message_status(message_log_id, 'queued')

    async def _record_direct_message(
        self,
        message_data: dict,
        instagram_id: str | None,
        url: str,
        content_type: str,
        status: str,
        error: str | None = None,
    ) -> int | None:
        message_id = message_data.get('item_id') or message_data.get('id')
        if not message_id:
            return None

        text = message_data.get('text') if isinstance(message_data.get('text'), str) else None
        link_data = message_data.get('link')
        if not text and isinstance(link_data, dict):
            text = link_data.get('text')

        async with async_session() as session:
            result = await session.execute(
                insert(DirectMessageLog)
                .values(
                    message_id=str(message_id),
                    instagram_id=str(instagram_id) if instagram_id else None,
                    text=text,
                    url=url or None,
                    content_type=content_type,
                    status=status,
                    error=error,
                    raw_data=message_data,
                    created_at=datetime.utcnow(),
                )
                .returning(DirectMessageLog.id)
            )
            await session.commit()
            return result.scalar_one_or_none()

    async def _update_direct_message_status(
        self,
        message_log_id: int | None,
        status: str,
        error: str | None = None,
    ) -> None:
        if not message_log_id:
            return

        async with async_session() as session:
            await session.execute(
                update(DirectMessageLog)
                .where(DirectMessageLog.id == message_log_id)
                .values(status=status, error=error)
            )
            await session.commit()

    async def send_direct_message(self, message_data: dict, text: str) -> tuple[bool, str | None]:
        if not self.page:
            self.logger.warning('No page available to send direct message')
            return False, 'No page available to send direct message'

        instagram_id = (
            message_data.get('user_id')
            or message_data.get('sender_id')
            or message_data.get('owner_id')
            or message_data.get('from_id')
        )
        thread_id = (
            message_data.get('thread_id')
            or message_data.get('thread_v2_id')
            or message_data.get('thread_key')
        )
        target_id = thread_id or instagram_id
        if not target_id:
            return False, 'No Instagram Direct thread id or user id available'

        if thread_id:
            sent, error = await self._send_direct_message_api(str(thread_id), text)
            if sent:
                return True, None
            self.logger.warning('Direct API send failed for thread %s: %s', thread_id, error)

        try:
            async with self._page_lock:
                thread_url = f'https://www.instagram.com/direct/t/{target_id}/'
                await self.page.goto(thread_url, timeout=30000)
                await self.page.wait_for_load_state('domcontentloaded', timeout=15000)
                await asyncio.sleep(random.uniform(2.0, 4.0))

                selectors = [
                    'textarea',
                    'div[role="textbox"][contenteditable="true"]',
                    'div[aria-label="Message"][contenteditable="true"]',
                    'div[contenteditable="true"]',
                ]
                input_locator = None
                for selector in selectors:
                    locator = self.page.locator(selector).last
                    try:
                        await locator.wait_for(state='visible', timeout=5000)
                        input_locator = locator
                        break
                    except Exception:
                        continue

                if input_locator is None:
                    return False, 'Instagram Direct message input was not found'

                await input_locator.click()
                await self.page.keyboard.insert_text(text)
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(random.uniform(2.0, 4.0))
            self.logger.info('Bind message sent to Instagram Direct target %s', target_id)
            return True, None
        except Exception as exc:
            self.logger.warning('Failed to send direct message to %s: %s', instagram_id, exc)
            return False, str(exc)

    async def _send_direct_message_api(self, thread_id: str, text: str) -> tuple[bool, str | None]:
        if not self.page:
            return False, 'No page available to send direct message'

        try:
            result = await self.page.evaluate(
                """async ({ threadId, text }) => {
                    const csrfCookie = document.cookie
                        .split('; ')
                        .find((item) => item.startsWith('csrftoken='));
                    const csrfToken = csrfCookie ? decodeURIComponent(csrfCookie.split('=').slice(1).join('=')) : '';
                    const clientContext = `${Date.now()}_${Math.floor(Math.random() * 1000000000)}`;
                    const body = new URLSearchParams({
                        thread_ids: JSON.stringify([threadId]),
                        text,
                        client_context: clientContext,
                        mutation_token: clientContext,
                    });
                    const response = await fetch('/api/v1/direct_v2/threads/broadcast/text/', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'X-CSRFToken': csrfToken,
                            'X-IG-App-ID': '936619743392459',
                            'Content-Type': 'application/x-www-form-urlencoded'
                        },
                        body: body.toString()
                    });
                    const responseText = await response.text();
                    return { ok: response.ok, status: response.status, text: responseText.slice(0, 500) };
                }""",
                {'threadId': thread_id, 'text': text},
            )
            if isinstance(result, dict) and result.get('ok'):
                self.logger.info('Direct API message sent to thread %s', thread_id)
                return True, None
            return False, f"HTTP {result.get('status') if isinstance(result, dict) else 'unknown'}: {result.get('text') if isinstance(result, dict) else result}"
        except Exception as exc:
            return False, str(exc)

    async def _accept_message_request(self, message_data: dict) -> tuple[bool, str | None]:
        thread_id = (
            message_data.get('thread_id')
            or message_data.get('thread_v2_id')
            or message_data.get('thread_key')
        )
        if not thread_id:
            return False, 'No thread id available for message request'
        if not self.page:
            return False, 'No page available to accept message request'

        try:
            result = await self.page.evaluate(
                """async (threadId) => {
                    const csrfCookie = document.cookie
                        .split('; ')
                        .find((item) => item.startsWith('csrftoken='));
                    const csrfToken = csrfCookie ? decodeURIComponent(csrfCookie.split('=').slice(1).join('=')) : '';
                    const headers = {
                        'X-CSRFToken': csrfToken,
                        'X-IG-App-ID': '936619743392459',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    };
                    const approveSingle = await fetch(`/api/v1/direct_v2/threads/${encodeURIComponent(threadId)}/approve/`, {
                        method: 'POST',
                        credentials: 'include',
                        headers,
                        body: ''
                    });
                    if (approveSingle.ok) {
                        return { ok: true, status: approveSingle.status, endpoint: 'approve' };
                    }
                    const approveMultiple = await fetch('/api/v1/direct_v2/threads/approve_multiple/', {
                        method: 'POST',
                        credentials: 'include',
                        headers,
                        body: new URLSearchParams({ thread_ids: JSON.stringify([threadId]) }).toString()
                    });
                    return {
                        ok: approveMultiple.ok,
                        status: approveMultiple.status,
                        endpoint: 'approve_multiple',
                        first_status: approveSingle.status,
                        text: await approveMultiple.text()
                    };
                }""",
                str(thread_id),
            )
            if isinstance(result, dict) and result.get('ok'):
                self.logger.info('Accepted Instagram message request thread %s', thread_id)
                return True, None
            return False, f"HTTP {result.get('status') if isinstance(result, dict) else 'unknown'}"
        except Exception as exc:
            return False, str(exc)

    async def _accept_visible_message_requests(self) -> None:
        if not self.page or not self._is_enabled(os.getenv('DIRECT_ACCEPT_REQUESTS_UI', 'true')):
            return

        request_links: list[str] = []
        accepted_snapshots: list[dict] = []
        try:
            async with self._page_lock:
                current_url = self.page.url
                await self.page.goto('https://www.instagram.com/direct/requests/', timeout=30000)
                await self.page.wait_for_load_state('domcontentloaded', timeout=15000)
                await asyncio.sleep(random.uniform(4.0, 6.0))

                result = await self.page.evaluate(
                    """async () => {
                        const acceptWords = [
                            'accept',
                            'allow',
                            'принять',
                            'разрешить',
                            'qabul',
                            'ruxsat'
                        ];
                        const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                        let accepted = 0;

                        for (let attempt = 0; attempt < 10; attempt += 1) {
                            const controls = Array.from(document.querySelectorAll('button, div[role="button"]'));
                            const control = controls.find((item) => {
                                const text = (item.innerText || item.textContent || '').trim().toLowerCase();
                                if (!text) return false;
                                return acceptWords.some((word) => text.includes(word));
                            });
                            if (!control) break;
                            control.click();
                            accepted += 1;
                            await sleep(1000);
                        }

                        const links = Array.from(document.querySelectorAll('a[href*="/direct/t/"]'))
                            .map((link) => link.href || link.getAttribute('href'))
                            .filter(Boolean);
                        const controls = Array.from(document.querySelectorAll('button, div[role="button"], a'));
                        const controlTexts = controls
                            .map((item) => (item.innerText || item.textContent || '').trim())
                            .filter(Boolean)
                            .slice(0, 20);
                        return {
                            accepted,
                            links: Array.from(new Set(links)),
                            control_count: controls.length,
                            control_texts: controlTexts,
                            url: window.location.href
                        };
                    }"""
                )

                accepted_count = int((result or {}).get('accepted') or 0) if isinstance(result, dict) else 0
                if isinstance(result, dict) and isinstance(result.get('links'), list):
                    request_links = [str(link) for link in result['links'] if link]
                control_count = int((result or {}).get('control_count') or 0) if isinstance(result, dict) else 0
                self.logger.info(
                    'Visible message request scan url=%s accepted=%s links=%s controls=%s texts=%s',
                    result.get('url') if isinstance(result, dict) else self.page.url,
                    accepted_count,
                    len(request_links),
                    control_count,
                    result.get('control_texts') if isinstance(result, dict) else None,
                )

                for request_link in request_links[:10]:
                    accepted_snapshot = await self._accept_visible_message_request_thread(request_link)
                    if accepted_snapshot is not None:
                        accepted_count += 1
                        accepted_snapshots.append(accepted_snapshot)

                if not request_links and not accepted_count:
                    opened = await self._open_first_visible_message_request_thread()
                    if opened:
                        accepted_snapshot = await self._accept_visible_message_request_thread(self.page.url)
                        if accepted_snapshot is not None:
                            accepted_count += 1
                            accepted_snapshots.append(accepted_snapshot)

                if accepted_count:
                    self.logger.info('Accepted %s visible Instagram message requests via UI fallback', accepted_count)
                elif request_links:
                    self.logger.info(
                        'Visible message request fallback found %s request threads but accepted none',
                        len(request_links),
                    )
                await self.page.goto('https://www.instagram.com/direct/inbox/', timeout=30000)
                await self.page.wait_for_load_state('domcontentloaded', timeout=15000)
                if current_url and '/direct/' not in current_url:
                    self.logger.debug('Direct request fallback returned from non-direct url=%s to inbox', current_url)
        except Exception as exc:
            self.logger.warning('Visible message request accept fallback failed: %s', exc)

        for snapshot in accepted_snapshots:
            await self._parse_direct_response(snapshot)

    async def _open_first_visible_message_request_thread(self) -> bool:
        if not self.page:
            return False

        try:
            result = await self.page.evaluate(
                """async () => {
                    const rejectWords = [
                        'accept', 'allow', 'принять', 'разрешить', 'qabul', 'ruxsat',
                        'delete', 'block', 'ignore', 'удалить', 'заблокировать',
                        'instagram', 'home', 'reels', 'search', 'explore', 'notifications',
                        'create', 'dashboard', 'profile', 'settings', 'more', 'meta',
                        'back', 'decide who can message you', 'hidden requests', 'delete all'
                    ];
                    const candidates = Array.from(document.querySelectorAll('div[role="button"], a[href]'))
                        .map((item) => {
                            const text = (item.innerText || item.textContent || '').trim().toLowerCase();
                            const rect = item.getBoundingClientRect();
                            return { item, text, rect };
                        })
                        .filter(({ text, rect }) => {
                            if (!text || rect.width < 120 || rect.height < 24) return false;
                            if (rejectWords.some((word) => text.includes(word))) return false;
                            return rect.top >= 0 && rect.top < window.innerHeight;
                        })
                        .sort((left, right) => {
                            const leftUnread = left.text.includes('unread') ? 0 : 1;
                            const rightUnread = right.text.includes('unread') ? 0 : 1;
                            if (leftUnread !== rightUnread) return leftUnread - rightUnread;
                            return left.rect.top - right.rect.top;
                        });
                    if (!candidates.length) {
                        return { opened: false, reason: 'no visible thread candidates' };
                    }
                    candidates[0].item.click();
                    await new Promise((resolve) => setTimeout(resolve, 2500));
                    return { opened: true, text: candidates[0].text };
                }"""
            )
            opened = bool(isinstance(result, dict) and result.get('opened'))
            self.logger.info(
                'Visible message request open fallback opened=%s url=%s detail=%s',
                opened,
                self.page.url,
                result,
            )
            return opened
        except Exception as exc:
            self.logger.warning('Visible message request open fallback failed: %s', exc)
            return False

    async def _accept_visible_message_request_thread(self, request_link: str) -> dict | None:
        if not self.page:
            return None

        try:
            await self.page.goto(request_link, timeout=30000)
            await self.page.wait_for_load_state('domcontentloaded', timeout=15000)
            await asyncio.sleep(random.uniform(1.0, 2.5))
            result = await self.page.evaluate(
                """async () => {
                    const acceptWords = ['accept', 'allow', 'принять', 'разрешить', 'qabul', 'ruxsat'];
                    const controls = Array.from(document.querySelectorAll('button, div[role="button"]'));
                    const control = controls.find((item) => {
                        const text = (item.innerText || item.textContent || '').trim().toLowerCase();
                        if (!text) return false;
                        return acceptWords.some((word) => text.includes(word));
                    });
                    if (!control) {
                        return { accepted: false, reason: 'accept control not found' };
                    }
                    control.click();
                    await new Promise((resolve) => setTimeout(resolve, 1500));
                    return { accepted: true };
                }"""
            )
            accepted = bool(isinstance(result, dict) and result.get('accepted'))
            if accepted:
                self.logger.info('Accepted visible Instagram message request thread %s', request_link)
                thread_snapshot = await self._fetch_direct_thread_snapshot(request_link)
                if thread_snapshot:
                    return thread_snapshot
                return {}
            else:
                self.logger.info(
                    'Visible Instagram message request thread was not accepted link=%s reason=%s',
                    request_link,
                    result.get('reason') if isinstance(result, dict) else 'unknown',
                )
            return None
        except Exception as exc:
            self.logger.warning('Visible message request thread accept failed link=%s error=%s', request_link, exc)
            return None

    async def _fetch_direct_thread_snapshot(self, request_link: str) -> dict | None:
        if not self.page:
            return None

        thread_id = self._extract_thread_id_from_direct_url(self.page.url) or self._extract_thread_id_from_direct_url(request_link)
        if not thread_id:
            return None

        try:
            data = await self.page.evaluate(
                """async (threadId) => {
                    const response = await fetch(`/api/v1/direct_v2/threads/${encodeURIComponent(threadId)}/?visual_message_return_type=unseen&limit=20`, {
                        credentials: 'include',
                        headers: { 'X-IG-App-ID': '936619743392459' }
                    });
                    if (!response.ok) {
                        return { ok: false, status: response.status, text: await response.text() };
                    }
                    const payload = await response.json();
                    payload._accepted_request_thread_id = threadId;
                    return { ok: true, payload };
                }""",
                thread_id,
            )
            if isinstance(data, dict) and data.get('ok') and isinstance(data.get('payload'), dict):
                candidate_count = len(self._collect_message_candidates(data['payload']))
                self.logger.info(
                    'Fetched accepted Direct request thread snapshot thread_id=%s candidates=%s',
                    thread_id,
                    candidate_count,
                )
                return data['payload']
            self.logger.warning(
                'Accepted Direct request thread snapshot fetch failed thread_id=%s result=%s',
                thread_id,
                data,
            )
            return await self._build_visible_thread_snapshot(thread_id)
        except Exception as exc:
            self.logger.warning('Accepted Direct request thread snapshot fetch failed thread_id=%s error=%s', thread_id, exc)
            return await self._build_visible_thread_snapshot(thread_id)

    async def _build_visible_thread_snapshot(self, thread_id: str) -> dict | None:
        if not self.page:
            return None

        try:
            data = await self.page.evaluate(
                """async () => {
                    const rejectWords = [
                        'instagram', 'home', 'reels', 'search', 'explore', 'notifications',
                        'create', 'dashboard', 'profile', 'settings', 'more', 'meta',
                        'accept', 'allow', 'delete', 'block', 'ignore', 'принять', 'разрешить'
                    ];
                    const texts = Array.from(document.querySelectorAll('h1, h2, span, div[dir="auto"]'))
                        .map((item) => (item.innerText || item.textContent || '').trim())
                        .filter(Boolean)
                        .filter((text, index, all) => all.indexOf(text) === index)
                        .slice(0, 80);
                    const username = texts.find((text) => /^[A-Za-z0-9._]{2,30}$/.test(text))
                        || texts.find((text) => text.length <= 40 && !rejectWords.some((word) => text.toLowerCase().includes(word)))
                        || '';
                    const messageText = texts.find((text) => {
                        const lower = text.toLowerCase();
                        if (text === username) return false;
                        if (rejectWords.some((word) => lower.includes(word))) return false;
                        return text.length <= 500;
                    }) || '';
                    return { username, message_text: messageText, texts };
                }"""
            )
            username = data.get('username') if isinstance(data, dict) else None
            message_text = data.get('message_text') if isinstance(data, dict) else None
            self.logger.info(
                'Built visible Direct thread snapshot fallback thread_id=%s username=%s text=%s',
                thread_id,
                username,
                message_text,
            )
            return {
                'thread': {
                    'thread_id': thread_id,
                    'users': [{'pk': thread_id, 'username': username} if username else {'pk': thread_id}],
                    'items': [{
                        'item_id': f'accepted_request_{thread_id}',
                        'user_id': thread_id,
                        'item_type': 'text',
                        'text': message_text or '',
                        'thread_id': thread_id,
                    }],
                }
            }
        except Exception as exc:
            self.logger.warning('Visible Direct thread snapshot fallback failed thread_id=%s error=%s', thread_id, exc)
            return None

    def _extract_thread_id_from_direct_url(self, value: str | None) -> str | None:
        if not value or '/direct/t/' not in value:
            return None
        thread_id = value.split('/direct/t/', 1)[1].split('/', 1)[0]
        return thread_id or None

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

    async def _create_user(self, instagram_id: str, instagram_username: str | None, session) -> None:
        default_limit = await self._get_system_setting('expired_daily_limit', '5')
        try:
            daily_limit = int(default_limit)
        except ValueError:
            daily_limit = 5

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

    async def _get_system_setting(self, key: str, default: str) -> str:
        async with async_session() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == key)
            result = await session.execute(stmt)
            value = result.scalar_one_or_none()
        return value if value is not None else default

    async def _has_any_message_log(self, message_id: str) -> bool:
        async with async_session() as session:
            stmt = select(DirectMessageLog.id).where(DirectMessageLog.message_id == str(message_id)).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def _passes_following_check(self, message_data: dict, message_log_id: int | None) -> bool:
        setting_value = await self._get_system_setting('check_instagram_following', 'false')
        if not self._is_enabled(setting_value):
            return True

        username = self._extract_content_owner_username(message_data)
        if not username:
            self.logger.warning('Instagram following check enabled, but content owner username was not found')
            await self._update_direct_message_status(
                message_log_id,
                'following_check_skipped',
                'Content owner username was not found',
            )
            return True

        is_following = await self._is_following_instagram_account(username)
        if is_following is False:
            self.logger.info('Content owner %s is not followed by the watcher account', username)
            await self._update_direct_message_status(
                message_log_id,
                'ignored',
                f'Watcher account is not following {username}',
            )
            return False

        if is_following is None:
            self.logger.warning('Could not verify following status for %s; queueing content', username)
            await self._update_direct_message_status(
                message_log_id,
                'following_check_unknown',
                f'Could not verify following status for {username}',
            )
        return True

    async def _is_following_instagram_account(self, username: str) -> bool | None:
        if not self.page:
            return None

        try:
            data = await self.page.evaluate(
                """async (username) => {
                    const response = await fetch(`/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`, {
                        credentials: 'include',
                        headers: { 'X-IG-App-ID': '936619743392459' }
                    });
                    if (!response.ok) {
                        return { ok: false, status: response.status };
                    }
                    return await response.json();
                }""",
                username,
            )
            if not isinstance(data, dict) or data.get('ok') is False:
                return None
            user_data = ((data.get('data') or {}).get('user') or {})
            if 'followed_by_viewer' in user_data:
                return bool(user_data.get('followed_by_viewer'))
        except Exception as exc:
            self.logger.warning('Following check failed for %s: %s', username, exc)
        return None

    def _extract_content_owner_username(self, value) -> str | None:
        if not isinstance(value, (dict, list)):
            return None

        def from_dict(data: dict) -> str | None:
            for key in ('owner', 'user', 'user_data', 'media_owner'):
                nested = data.get(key)
                if isinstance(nested, dict) and nested.get('username'):
                    return nested['username']
            if data.get('owner_username'):
                return data['owner_username']
            if data.get('username') and data.get('item_type') in ('clip', 'media_share', 'raven_media'):
                return data['username']
            return None

        if isinstance(value, dict):
            username = from_dict(value)
            if username:
                return username
            for nested in value.values():
                username = self._extract_content_owner_username(nested)
                if username:
                    return username
        else:
            for item in value:
                username = self._extract_content_owner_username(item)
                if username:
                    return username
        return None

    def _is_enabled(self, value: str | bool | None) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'enabled')

    async def _has_processed_message(self, message_id: str) -> bool:
        async with async_session() as session:
            stmt = (
                select(DirectMessageLog.id)
                .where(
                    DirectMessageLog.message_id == str(message_id),
                    DirectMessageLog.status != 'snapshot',
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def _has_recent_bind_prompt(self, instagram_id: str) -> bool:
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        async with async_session() as session:
            stmt = (
                select(DirectMessageLog.id)
                .where(
                    DirectMessageLog.instagram_id == instagram_id,
                    DirectMessageLog.status == 'needs_telegram',
                    DirectMessageLog.created_at >= cutoff,
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None
