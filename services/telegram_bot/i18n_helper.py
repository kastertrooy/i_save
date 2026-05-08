import json
from pathlib import Path
from typing import Dict

# Кэш для загруженных переводов
_cache: Dict[str, Dict[str, str]] = {}


def get_text(key: str, language: str, **kwargs) -> str:
    """
    Получает текст перевода по ключу и языку.
    Кэширует переводы в памяти.
    Fallback: если язык не найден -> 'ru'
    Если ключ не найден -> русский текст
    Поддержка подстановки переменных через .format(**kwargs)
    """
    if language not in _cache:
        file_path = Path(__file__).parent.parent / 'shared' / 'i18n' / f'{language}.json'
        if not file_path.exists():
            language = 'ru'  # Fallback на русский
            file_path = Path(__file__).parent.parent / 'shared' / 'i18n' / 'ru.json'
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
            ru_path = Path(__file__).parent.parent / 'shared' / 'i18n' / 'ru.json'
            try:
                with open(ru_path, 'r', encoding='utf-8') as f:
                    _cache['ru'] = json.load(f)
            except FileNotFoundError:
                return f"Missing translation for {key}"
        text = _cache['ru'].get(key, f"Missing translation for {key}")

    # Подстановка переменных
    return text.format(**kwargs) if kwargs else text