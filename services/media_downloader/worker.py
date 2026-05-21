import asyncio
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select, update
from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import ContentQueue, MediaCache, SystemSetting
from shared.logger import get_logger
from shared.url_utils import normalize_instagram_url
from .converter import cleanup_files, extract_audio
from .downloader import download_media, download_carousel
from .storage_uploader import upload_to_storage, save_to_cache


class DownloaderWorker:
    def __init__(self) -> None:
        self.logger = get_logger('media_downloader')
        self.download_root = Path(settings.temp_download_path or 'temp') / 'downloads'
        self.download_root.mkdir(parents=True, exist_ok=True)

    async def run(self) -> None:
        self.logger.info('Media downloader worker started')
        while True:
            try:
                items = await self._fetch_next_items()
                if not items:
                    await asyncio.sleep(15)
                    continue

                for item in items:
                    await self._process_item(item)

            except asyncio.CancelledError:
                self.logger.info('Worker cancelled')
                break
            except Exception as exc:
                self.logger.exception('Worker error: %s', exc)
                await asyncio.sleep(15)

    async def _fetch_next_items(self):
        async with async_session() as session:
            order_column = getattr(ContentQueue, 'created_at', ContentQueue.id)
            stmt = (
                select(ContentQueue)
                .where(ContentQueue.status == 'pending')
                .order_by(order_column)
                .with_for_update(skip_locked=True)
                .limit(5)
            )
            result = await session.execute(stmt)
            items = result.scalars().all()

            if not items:
                return []

            item_ids = [item.id for item in items]
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id.in_(item_ids))
                .values(status='downloading')
            )
            await session.commit()

        return items

    async def _process_item(self, item) -> None:
        self.logger.info('Processing content_queue id=%s', item.id)
        paths = []
        normalized_url = normalize_instagram_url(item.url)
        try:
            if await self._cache_exists(normalized_url):
                self.logger.info('Skipping item %s because it is already cached', item.id)
                if normalized_url != item.url:
                    await self._update_item_url(item.id, normalized_url)
                await self._mark_downloaded(item.id)
                return

            if item.content_type == 'story' and not await self._check_story_availability(item.url):
                await self._mark_expired(item.id)
                self.logger.info('Marked story content_queue id=%s as expired', item.id)
                return

            if item.content_type == 'carousel':
                urls = item.carousel_urls or []
                paths = await asyncio.to_thread(download_carousel, urls, item.id)
            else:
                downloaded = await asyncio.to_thread(download_media, normalized_url, item.content_type, item.id)
                if not downloaded:
                    raise RuntimeError('Download returned no file for queue id %s' % item.id)
                paths = [downloaded]

            if not paths:
                raise RuntimeError('No downloaded files for queue id %s' % item.id)

            file_ids = {}
            album_file_ids = []
            cleanup_paths = list(paths)
            for path in list(paths):
                ext = Path(path).suffix.lower()
                content_type = item.content_type
                is_video_file = ext not in ('.jpg', '.jpeg', '.png', '.webp', '.mp3', '.wav', '.ogg', '.aac')
                if item.content_type == 'carousel':
                    if ext in ('.jpg', '.jpeg', '.png', '.webp'):
                        content_type = 'photo'
                    elif ext in ('.mp3', '.wav', '.ogg', '.aac'):
                        content_type = 'audio'
                    else:
                        content_type = 'video'

                upload_result = await upload_to_storage(path, content_type, item.id)
                file_ids.update(upload_result)
                if item.content_type == 'carousel':
                    album_file_ids.append(upload_result)

                if is_video_file and content_type in ('video', 'reel', 'story'):
                    audio_path = await asyncio.to_thread(extract_audio, path)
                    audio_ids = await upload_to_storage(audio_path, 'audio', item.id)
                    file_ids.update(audio_ids)
                    cleanup_paths.append(audio_path)

            if album_file_ids:
                file_ids['file_ids'] = album_file_ids

            storage_group_id = await self._get_storage_group_id()
            size_mb = self._calculate_size_mb(cleanup_paths)
            if normalized_url != item.url:
                await self._update_item_url(item.id, normalized_url)
            await save_to_cache(normalized_url, file_ids, storage_group_id, size_mb)
            await self._mark_downloaded(item.id)
            await asyncio.to_thread(cleanup_files, cleanup_paths)
            self.logger.info('Content queue id=%s downloaded successfully', item.id)

        except Exception as exc:
            self.logger.exception('Error processing content_queue id=%s: %s', item.id, exc)
            await self._handle_processing_error(item)

    async def _cache_exists(self, url: str) -> bool:
        normalized_url = normalize_instagram_url(url)
        async with async_session() as session:
            stmt = select(MediaCache).where(MediaCache.original_url == normalized_url)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def _update_item_url(self, item_id: int, url: str) -> None:
        async with async_session() as session:
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item_id)
                .values(url=url)
            )
            await session.commit()

    async def _check_story_availability(self, url: str) -> bool:
        try:
            request = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(request, timeout=15) as response:
                return response.status == 200
        except (HTTPError, URLError, TimeoutError, Exception) as exc:
            self.logger.warning('Story availability check failed for %s: %s', url, exc)
            return False

    async def _get_storage_group_id(self) -> int:
        storage_group_id = os.getenv('STORAGE_GROUP_ID')
        if storage_group_id:
            return int(storage_group_id)

        async with async_session() as session:
            stmt = select(SystemSetting.value).where(SystemSetting.key == 'telegram_storage_group_id')
            result = await session.execute(stmt)
            storage_group_id = result.scalar_one_or_none()

        if storage_group_id is None:
            raise RuntimeError('telegram_storage_group_id not configured in system_settings')

        return int(storage_group_id)

    def _calculate_size_mb(self, paths: list) -> float:
        total = 0.0
        for path in paths:
            file_path = Path(path)
            if file_path.exists():
                total += file_path.stat().st_size / (1024 * 1024)
        return total

    async def _mark_downloaded(self, item_id: int) -> None:
        async with async_session() as session:
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item_id)
                .values(status='downloaded')
            )
            await session.commit()

    async def _mark_expired(self, item_id: int) -> None:
        async with async_session() as session:
            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item_id)
                .values(status='expired')
            )
            await session.commit()

    async def _handle_processing_error(self, item) -> None:
        async with async_session() as session:
            retry_count = (item.retry_count or 0) + 1
            values = {'retry_count': retry_count}
            if retry_count >= 3:
                values['status'] = 'failed'
            else:
                values['status'] = 'pending'

            await session.execute(
                update(ContentQueue)
                .where(ContentQueue.id == item.id)
                .values(**values)
            )
            await session.commit()
