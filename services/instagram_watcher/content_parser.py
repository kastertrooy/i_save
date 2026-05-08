import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


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
        main_url = carousel_urls[0]
        return main_url, carousel_urls

    # Одиночные значения
    for key in ('url', 'video_url', 'image_url', 'photo_url'):
        if message_data.get(key):
            main_url = message_data[key]
            break

    return main_url, []


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
