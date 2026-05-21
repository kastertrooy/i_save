from urllib.parse import urlparse, urlunparse


def normalize_instagram_url(url: str) -> str:
    if not url:
        return ''

    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()

    host = parsed.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]

    if host not in ('instagram.com', 'instagr.am'):
        return url.strip()

    path_parts = [part for part in parsed.path.split('/') if part]
    if len(path_parts) >= 2 and path_parts[0] in ('p', 'reel', 'tv', 'stories'):
        path = f'/{path_parts[0]}/{path_parts[1]}/'
    else:
        path = parsed.path if parsed.path.endswith('/') else f'{parsed.path}/'

    return urlunparse(('https', 'www.instagram.com', path, '', '', ''))
