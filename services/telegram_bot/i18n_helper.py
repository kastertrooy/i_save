import json
from pathlib import Path
from typing import Dict

# Кэш для загруженных переводов
_cache: Dict[str, Dict[str, str]] = {}


def _i18n_path(language: str) -> Path:
    candidates = [
        Path('/shared/i18n') / f'{language}.json',
        Path(__file__).resolve().parents[2] / 'shared' / 'i18n' / f'{language}.json',
        Path.cwd() / 'shared' / 'i18n' / f'{language}.json',
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def get_text(key: str, language: str, **kwargs) -> str:
    """
    Получает текст перевода по ключу и языку.
    Кэширует переводы в памяти.
    Fallback: если язык не найден -> 'ru'
    Если ключ не найден -> русский текст
    Поддержка подстановки переменных через .format(**kwargs)
    """
    if language not in _cache:
        file_path = _i18n_path(language)
        if not file_path.exists():
            language = 'ru'  # Fallback на русский
            file_path = _i18n_path('ru')
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                _cache[language] = json.load(f)
        except FileNotFoundError:
            raise ValueError(f"Translation file for language '{language}' not found")

    translations = _cache[language]
    text = translations.get(key)

    if text is None:
        # Fallback на русский
        if 'ru' not in _cache:
            ru_path = _i18n_path('ru')
            try:
                with open(ru_path, 'r', encoding='utf-8') as f:
                    _cache['ru'] = json.load(f)
            except FileNotFoundError:
                return f"Missing translation for {key}"
        text = _cache['ru'].get(key, f"Missing translation for {key}")

    # Подстановка переменных
    return text.format(**kwargs) if kwargs else text
