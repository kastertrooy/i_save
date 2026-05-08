from pathlib import Path
from typing import List

from yt_dlp import YoutubeDL

from shared.config import settings
from shared.logger import get_logger

logger = get_logger('media_downloader')


def _get_download_folder(queue_id: int) -> Path:
    base = Path(settings.temp_download_path or 'temp') / 'downloads' / str(queue_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _should_skip_size(info: dict) -> bool:
    max_size_mb = settings.max_video_size_mb
    size_bytes = info.get('filesize') or info.get('filesize_approx') or 0
    if not size_bytes:
        return False
    size_mb = size_bytes / (1024 * 1024)
    return size_mb > max_size_mb


def _extract_downloaded_filepath(info: dict, output_path: Path, prefix: str) -> str:
    if 'requested_downloads' in info and info['requested_downloads']:
        return info['requested_downloads'][0].get('filepath')
    ext = info.get('ext') or 'mp4'
    return str(output_path / f'{prefix}.{ext}')


def download_media(url: str, content_type: str, queue_id: int) -> str:
    """
    Скачать медиа через yt-dlp в temp/downloads/{queue_id}/.
    Возвращает путь к файлу или пустую строку, если файл пропущен.
    """
    output_path = _get_download_folder(queue_id)
    logger.info('Downloading media from %s to %s', url, output_path)

    info_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
        'ignoreerrors': True,
    }
    with YoutubeDL(info_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            logger.warning('Unable to probe media size for %s: %s', url, exc)
            info = None

    if info and _should_skip_size(info):
        size_bytes = info.get('filesize') or info.get('filesize_approx') or 0
        size_mb = size_bytes / (1024 * 1024)
        logger.warning('Skipping %s because it exceeds max_video_size_mb: %.2f MB', url, size_mb)
        return ''

    template = str(output_path / 'media.%(ext)s')
    ydl_opts = {
        'outtmpl': template,
        'format': 'best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'continuedl': True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not info:
        logger.error('Failed to download media from %s', url)
        return ''

    filepath = _extract_downloaded_filepath(info, output_path, 'media')
    logger.info('Downloaded media to %s', filepath)
    return filepath


def download_carousel(urls: List[str], queue_id: int) -> List[str]:
    """
    Скачать каждый URL карусели индивидуально.
    """
    paths: List[str] = []
    for index, url in enumerate(urls, start=1):
        output_path = _get_download_folder(queue_id)
        filename_prefix = f'media_{index}'
        logger.info('Downloading carousel item %s/%s: %s', index, len(urls), url)

        template = str(output_path / f'{filename_prefix}.%(ext)s')
        ydl_opts = {
            'outtmpl': template,
            'format': 'best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'continuedl': True,
        }

        if settings.max_video_size_mb:
            try:
                probe_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'skip_download': True,
                    'noplaylist': True,
                    'ignoreerrors': True,
                }
                with YoutubeDL(probe_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if info and _should_skip_size(info):
                    size_bytes = info.get('filesize') or info.get('filesize_approx') or 0
                    size_mb = size_bytes / (1024 * 1024)
                    logger.warning('Skipping carousel item %s because it exceeds max_video_size_mb: %.2f MB', url, size_mb)
                    continue
            except Exception as exc:
                logger.warning('Unable to probe carousel media size for %s: %s', url, exc)

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            logger.warning('Failed to download carousel media from %s', url)
            continue

        filepath = _extract_downloaded_filepath(info, output_path, filename_prefix)
        paths.append(filepath)
        logger.info('Downloaded carousel item to %s', filepath)

    return paths
