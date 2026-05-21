import json
import re
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from shared.url_utils import normalize_instagram_url


URL_RE = re.compile(r'https?://[^\s<>"\']+')


def _clean_url(url: str) -> str:
    return (url or '').strip().rstrip('.,);]')


def parse_content_type(message_data: dict) -> str:
    """
    Определяет тип контента по данным сообщения.
    """
    if not isinstance(message_data, dict):
        return 'photo'

    if message_data.get('type') == 'story' or message_data.get('is_story'):
        return 'story'

    if message_data.get('type') == 'reel' or message_data.get('is_reel'):
        return 'reel'

    if message_data.get('item_type') in ('clip', 'media_share'):
        return 'reel'

    if message_data.get('carousel_urls') or message_data.get('carousel_media') or message_data.get('items'):
        return 'carousel'

    if message_data.get('type') == 'video' or message_data.get('is_video'):
        return 'video'

    if message_data.get('type') == 'photo' or message_data.get('is_image') or message_data.get('is_photo'):
        return 'photo'

    # Fallback на основе полей
    if message_data.get('video_url') or message_data.get('video'):
        return 'video'
    if message_data.get('image_url') or message_data.get('photo_url'):
        return 'photo'

    return 'photo'


def extract_urls(message_data: dict) -> tuple[str, list]:
    """
    Возвращает основной URL и список URL для карусели.
    """
    if not isinstance(message_data, dict):
        return '', []

    carousel_urls = []
    main_url = ''

    if message_data.get('carousel_urls'):
        carousel_urls = list(message_data['carousel_urls'])
    elif message_data.get('carousel_media'):
        carousel_media = message_data['carousel_media']
        if isinstance(carousel_media, list):
            carousel_urls = [item.get('url') for item in carousel_media if isinstance(item, dict) and item.get('url')]
    elif message_data.get('items'):
        items = message_data['items']
        if isinstance(items, list):
            carousel_urls = [item.get('url') for item in items if isinstance(item, dict) and item.get('url')]

    if carousel_urls:
        carousel_urls = [normalize_instagram_url(url) for url in carousel_urls]
        main_url = carousel_urls[0]
        return main_url, carousel_urls

    # Одиночные значения
    for key in ('url', 'video_url', 'image_url', 'photo_url'):
        if message_data.get(key):
            main_url = normalize_instagram_url(message_data[key])
            break

    if not main_url and isinstance(message_data.get('link'), dict):
        link_data = message_data['link']
        for key in ('url', 'link_url', 'href', 'text'):
            if link_data.get(key):
                value = link_data[key]
                if key == 'text':
                    match = URL_RE.search(value)
                    if not match:
                        continue
                    value = match.group(0)
                main_url = normalize_instagram_url(_clean_url(value))
                break

    if not main_url and isinstance(message_data.get('clip'), dict):
        clip_data = message_data['clip']
        nested_clip = clip_data.get('clip') if isinstance(clip_data.get('clip'), dict) else clip_data
        clip_code = nested_clip.get('code') or nested_clip.get('shortcode')
        if clip_code:
            main_url = normalize_instagram_url(f'https://www.instagram.com/reel/{clip_code}/')

    if not main_url and isinstance(message_data.get('media_share'), dict):
        media_share = message_data['media_share']
        share_code = media_share.get('code') or media_share.get('shortcode')
        if share_code:
            main_url = normalize_instagram_url(f'https://www.instagram.com/p/{share_code}/')

    if not main_url and isinstance(message_data.get('direct_media_share'), dict):
        media_data = message_data['direct_media_share'].get('media')
        if isinstance(media_data, dict):
            share_code = media_data.get('code') or media_data.get('shortcode')
            product_type = media_data.get('product_type')
            if share_code and product_type == 'clips':
                main_url = normalize_instagram_url(f'https://www.instagram.com/reel/{share_code}/')
            elif share_code:
                main_url = normalize_instagram_url(f'https://www.instagram.com/p/{share_code}/')

    if not main_url and isinstance(message_data.get('text'), str):
        match = URL_RE.search(message_data['text'])
        if match:
            main_url = normalize_instagram_url(_clean_url(match.group(0)))

    return normalize_instagram_url(main_url), []


def check_file_size(url: str) -> float | None:
    """
    Проверяет размер файла по HEAD запросу и возвращает размер в МБ.
    """
    if not url:
        return None

    try:
        request = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=10) as response:
            content_length = response.getheader('Content-Length')
            if content_length is None:
                return None
            size_bytes = int(content_length)
            return size_bytes / (1024 * 1024)
    except (HTTPError, URLError, ValueError):
        return None
    except Exception:
        return None
