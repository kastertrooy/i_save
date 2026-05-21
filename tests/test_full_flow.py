import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://postgres:postgres@postgres:5432/instatgbot')
os.environ.setdefault('REDIS_URL', 'redis://redis:6379/0')
os.environ.setdefault('ENCRYPTION_KEY', 'test-key')
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret')
os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123:test')
os.environ.setdefault('TELEGRAM_BOT_USERNAME', 'test_bot')
os.environ.setdefault('ADMIN_TELEGRAM_CHAT_ID', '1')
os.environ.setdefault('DOCKER_SOCKET', '/var/run/docker.sock')
os.environ.setdefault('TEMP_DOWNLOAD_PATH', '/tmp/instatg-tests')
os.environ.setdefault('STORAGE_GROUP_ID', '1')

from sqlalchemy import delete, insert, select, text, update

from services.instagram_watcher.direct_reader import DirectReader
from services.media_downloader.worker import DownloaderWorker
from services.media_sender.worker import SenderWorker
from services.telegram_bot.handlers.start import handle_start_with_token
from shared.database.connection import async_session, engine
from shared.database.models import (
    Base,
    ContentQueue,
    DeliveryLog,
    DirectMessageLog,
    MediaCache,
    SubscriptionLog,
    SystemSetting,
    TelegramStorageGroup,
    User,
)


class FakeTelegramMessage:
    def __init__(self, text: str, chat_id: int = 123456789) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=chat_id, username='test_telegram')
        self.replies = []

    async def reply(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeBot:
    sent = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.session = SimpleNamespace(close=AsyncMock())

    async def send_video(self, chat_id, video):
        self.sent.append(('video', chat_id, video))

    async def send_audio(self, chat_id, audio):
        self.sent.append(('audio', chat_id, audio))

    async def send_photo(self, chat_id, photo):
        self.sent.append(('photo', chat_id, photo))

    async def send_document(self, chat_id, document):
        self.sent.append(('document', chat_id, document))

    async def send_media_group(self, chat_id, media):
        self.sent.append(('media_group', chat_id, media))

    async def send_message(self, chat_id, text):
        self.sent.append(('message', chat_id, text))


class FullFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text('alter table media_cache add column if not exists telegram_file_ids json'))
            await conn.execute(text('alter table media_cache add column if not exists telegram_file_id_document varchar'))
            await conn.execute(text('alter table delivery_logs alter column content_queue_id drop not null'))

        self.prefix = f'test_user_{int(datetime.utcnow().timestamp() * 1000000)}'
        await self._cleanup()
        async with async_session() as session:
            await session.execute(text(
                "insert into telegram_storage_groups(id, name, telegram_group_id) "
                "values (1, 'test-storage', -100111222333) "
                "on conflict (id) do nothing"
            ))
            await session.execute(text(
                "insert into system_settings(key, value) values ('free_trial_days', '30') "
                "on conflict (key) do update set value = excluded.value"
            ))
            await session.commit()

    async def asyncTearDown(self):
        await self._cleanup()
        await engine.dispose()

    async def _cleanup(self):
        async with async_session() as session:
            users = await session.execute(select(User.id).where(User.instagram_id.like(f'{self.prefix}%')))
            user_ids = [row[0] for row in users.all()]
            await session.execute(delete(DeliveryLog).where(DeliveryLog.user_id.in_(user_ids or [-1])))
            await session.execute(delete(SubscriptionLog).where(SubscriptionLog.user_id.in_(user_ids or [-1])))
            await session.execute(delete(ContentQueue).where(ContentQueue.instagram_id.like(f'{self.prefix}%')))
            await session.execute(delete(DirectMessageLog).where(DirectMessageLog.instagram_id.like(f'{self.prefix}%')))
            await session.execute(delete(MediaCache).where(MediaCache.original_url.like('https://www.instagram.com/%test%')))
            await session.execute(delete(User).where(User.instagram_id.like(f'{self.prefix}%')))
            await session.commit()

    async def _simulate_instagram_message(self, instagram_id: str, url: str, content_type: str = 'video', **extra):
        reader = DirectReader()
        reader.send_direct_message = AsyncMock(return_value=(True, None))
        message = {
            'item_id': f'msg_{instagram_id}_{datetime.utcnow().timestamp()}',
            'user_id': instagram_id,
            'type': content_type,
            'url': url,
            **extra,
        }
        with patch('services.instagram_watcher.direct_reader.asyncio.sleep', new=AsyncMock()):
            await reader.on_new_message(message)
        return reader

    async def _get_user(self, instagram_id: str):
        async with async_session() as session:
            result = await session.execute(select(User).where(User.instagram_id == instagram_id))
            return result.scalar_one()

    async def _get_queue(self, instagram_id: str):
        async with async_session() as session:
            result = await session.execute(select(ContentQueue).where(ContentQueue.instagram_id == instagram_id).order_by(ContentQueue.id))
            return result.scalars().all()

    async def _bind_user(self, token: str, chat_id: int = 123456789):
        message = FakeTelegramMessage(f'/start {token}', chat_id=chat_id)
        async with async_session() as session:
            await handle_start_with_token(message, session)
        return message

    async def _process_downloader_once(self, instagram_id: str, expected_status='downloaded'):
        worker = DownloaderWorker()
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / 'video.mp4'
            audio_path = Path(tmp) / 'video.mp4_audio.mp3'
            video_path.write_bytes(b'video')
            audio_path.write_bytes(b'audio')

            async def fake_upload(path, content_type, queue_id):
                if content_type == 'audio':
                    return {'file_id_audio': f'audio-{queue_id}'}
                if content_type == 'photo':
                    return {'file_id_photo': f'photo-{Path(path).stem}-{queue_id}'}
                return {'file_id_video': f'video-{queue_id}', 'file_id_document': f'document-{queue_id}'}

            with (
                patch('services.media_downloader.worker.download_media', return_value=str(video_path)),
                patch('services.media_downloader.worker.extract_audio', return_value=str(audio_path)),
                patch('services.media_downloader.worker.upload_to_storage', new=AsyncMock(side_effect=fake_upload)),
                patch('services.media_downloader.worker.cleanup_files'),
            ):
                async with async_session() as session:
                    await session.execute(
                        update(ContentQueue)
                        .where(ContentQueue.instagram_id == instagram_id)
                        .where(ContentQueue.status == 'pending')
                        .values(status='downloading')
                    )
                    result = await session.execute(select(ContentQueue).where(ContentQueue.instagram_id == instagram_id))
                    items = result.scalars().all()
                    await session.commit()

                self.assertTrue(items)
                for item in items:
                    await worker._process_item(item)

        async with async_session() as session:
            statuses = (
                await session.execute(
                    select(ContentQueue.status).where(ContentQueue.instagram_id == instagram_id)
                )
            ).scalars().all()
            self.assertTrue(all(status == expected_status for status in statuses), statuses)

    async def _process_sender_once(self):
        FakeBot.sent = []
        worker = SenderWorker()
        with patch('services.media_sender.sender.Bot', FakeBot), patch('services.media_sender.worker.Bot', FakeBot):
            items = await worker._fetch_next_items()
            self.assertTrue(items)
            for item in items:
                await worker._process_item(item)

    async def test_new_user_full_flow(self):
        instagram_id = f'{self.prefix}_new'
        url = 'https://www.instagram.com/reel/test123/'

        await self._simulate_instagram_message(instagram_id, url, 'video')
        user = await self._get_user(instagram_id)
        self.assertIsNone(user.telegram_chat_id)
        self.assertIsNotNone(user.bind_token)
        queue = await self._get_queue(instagram_id)
        self.assertEqual(queue[0].status, 'no_telegram')

        await self._bind_user(user.bind_token)
        user = await self._get_user(instagram_id)
        self.assertEqual(user.telegram_chat_id, 123456789)
        self.assertEqual(user.subscription_status, 'free_trial')
        queue = await self._get_queue(instagram_id)
        self.assertEqual(queue[0].status, 'pending')

        await self._process_downloader_once(instagram_id)
        async with async_session() as session:
            cache = (await session.execute(select(MediaCache).where(MediaCache.original_url == url))).scalar_one()
            self.assertIsNotNone(cache.telegram_file_id_video)
            self.assertIsNotNone(cache.telegram_file_id_audio)
            self.assertIsNotNone(cache.telegram_file_id_document)

        await self._process_sender_once()
        queue = await self._get_queue(instagram_id)
        self.assertEqual(queue[0].status, 'done')
        async with async_session() as session:
            deliveries = (
                await session.execute(
                    select(DeliveryLog)
                    .where(DeliveryLog.status == 'success')
                    .where(DeliveryLog.user_id == user.id)
                )
            ).scalars().all()
            self.assertEqual(len(deliveries), 1)
        user = await self._get_user(instagram_id)
        self.assertEqual(user.daily_downloads_today, 1)
        self.assertTrue(any(sent[0] == 'video' and sent[1] == 123456789 for sent in FakeBot.sent))
        self.assertTrue(any(sent[0] == 'audio' and sent[1] == 123456789 for sent in FakeBot.sent))
        self.assertTrue(any(sent[0] == 'document' and sent[1] == 123456789 for sent in FakeBot.sent))

    async def test_initial_snapshot_processes_pending_text_request_without_telegram(self):
        instagram_id = f'{self.prefix}_pending_text'
        reader = DirectReader()
        reader._fetch_direct_snapshots = AsyncMock(return_value=[{
            '_is_pending_inbox_snapshot': True,
            'threads': [{
                'thread_id': f'thread_{instagram_id}',
                'users': [{'pk': instagram_id, 'username': 'pending_user'}],
                'items': [{
                    'item_id': f'msg_{instagram_id}_old',
                    'user_id': instagram_id,
                    'item_type': 'text',
                    'text': 'hello',
                    'timestamp': '1',
                }],
            }],
        }])
        reader._accept_message_request = AsyncMock(return_value=(True, None))
        reader.send_direct_message = AsyncMock(return_value=(True, None))

        with patch('services.instagram_watcher.direct_reader.asyncio.sleep', new=AsyncMock()):
            await reader._load_initial_inbox_snapshot()

        user = await self._get_user(instagram_id)
        self.assertIsNone(user.telegram_chat_id)
        self.assertEqual(user.instagram_username, 'pending_user')
        self.assertIsNotNone(user.bind_token)
        self.assertEqual(await self._get_queue(instagram_id), [])
        reader._accept_message_request.assert_called_once()
        reader.send_direct_message.assert_called_once()
        self.assertIn('t.me/', reader.send_direct_message.call_args.args[1])

    async def test_existing_user_queues_pending_immediately(self):
        instagram_id = f'{self.prefix}_existing'
        async with async_session() as session:
            await session.execute(insert(User).values(
                instagram_id=instagram_id,
                telegram_chat_id=123456789,
                language='ru',
                subscription_status='active',
                daily_limit=5,
                daily_downloads_today=0,
            ))
            await session.commit()

        await self._simulate_instagram_message(instagram_id, 'https://www.instagram.com/reel/test-existing/', 'video')
        queue = await self._get_queue(instagram_id)
        self.assertEqual(queue[0].status, 'pending')
        await self._process_downloader_once(instagram_id)
        await self._process_sender_once()
        self.assertEqual((await self._get_queue(instagram_id))[0].status, 'done')

    async def test_expired_subscription_limit(self):
        instagram_id = f'{self.prefix}_expired'
        async with async_session() as session:
            await session.execute(insert(User).values(
                instagram_id=instagram_id,
                telegram_chat_id=123456789,
                language='ru',
                subscription_status='expired',
                daily_limit=5,
                daily_downloads_today=0,
                daily_downloads_updated_at=datetime.utcnow(),
            ))
            await session.commit()

        await self._simulate_instagram_message(instagram_id, 'https://www.instagram.com/reel/test-under-limit/', 'video')
        self.assertEqual((await self._get_queue(instagram_id))[0].status, 'pending')

        async with async_session() as session:
            await session.execute(delete(ContentQueue).where(ContentQueue.instagram_id == instagram_id))
            await session.execute(update(User).where(User.instagram_id == instagram_id).values(daily_downloads_today=5))
            await session.commit()

        reader = await self._simulate_instagram_message(instagram_id, 'https://www.instagram.com/reel/test-over-limit/', 'video')
        self.assertEqual(await self._get_queue(instagram_id), [])
        self.assertTrue(reader.send_direct_message.called)

    async def test_content_types_carousel_story_and_video_audio(self):
        instagram_id = f'{self.prefix}_types'
        async with async_session() as session:
            await session.execute(insert(User).values(
                instagram_id=instagram_id,
                telegram_chat_id=123456789,
                language='ru',
                subscription_status='active',
                daily_limit=5,
                daily_downloads_today=0,
            ))
            await session.execute(insert(ContentQueue).values(
                instagram_id=instagram_id,
                url='https://www.instagram.com/p/test-carousel/',
                content_type='carousel',
                carousel_urls=['https://cdn.test/1.jpg', 'https://cdn.test/2.jpg'],
                status='pending',
                retry_count=0,
            ))
            await session.commit()

        worker = DownloaderWorker()
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / 'one.jpg', Path(tmp) / 'two.jpg']
            for file_path in files:
                file_path.write_bytes(b'photo')

            async def fake_upload(path, content_type, queue_id):
                return {'file_id_photo': f'photo-{Path(path).stem}'}

            with (
                patch('services.media_downloader.worker.download_carousel', return_value=[str(path) for path in files]),
                patch('services.media_downloader.worker.upload_to_storage', new=AsyncMock(side_effect=fake_upload)),
                patch('services.media_downloader.worker.cleanup_files'),
            ):
                async with async_session() as session:
                    await session.execute(
                        update(ContentQueue)
                        .where(ContentQueue.instagram_id == instagram_id)
                        .where(ContentQueue.status == 'pending')
                        .values(status='downloading')
                    )
                    result = await session.execute(select(ContentQueue).where(ContentQueue.instagram_id == instagram_id))
                    items = result.scalars().all()
                    await session.commit()
                await worker._process_item(items[0])

        async with async_session() as session:
            cache = (await session.execute(select(MediaCache).where(MediaCache.original_url == 'https://www.instagram.com/p/test-carousel/'))).scalar_one()
            self.assertEqual(len(cache.telegram_file_ids), 2)
            await session.execute(delete(ContentQueue).where(ContentQueue.instagram_id == instagram_id))
            await session.execute(insert(ContentQueue).values(
                instagram_id=instagram_id,
                url='https://www.instagram.com/stories/test-expired/',
                content_type='story',
                status='pending',
                retry_count=0,
            ))
            await session.commit()

        with patch.object(DownloaderWorker, '_check_story_availability', new=AsyncMock(return_value=False)):
            async with async_session() as session:
                await session.execute(
                    update(ContentQueue)
                    .where(ContentQueue.instagram_id == instagram_id)
                    .where(ContentQueue.status == 'pending')
                    .values(status='downloading')
                )
                result = await session.execute(select(ContentQueue).where(ContentQueue.instagram_id == instagram_id))
                items = result.scalars().all()
                await session.commit()
            await worker._process_item(items[0])
        self.assertEqual((await self._get_queue(instagram_id))[0].status, 'expired')


if __name__ == '__main__':
    unittest.main()
