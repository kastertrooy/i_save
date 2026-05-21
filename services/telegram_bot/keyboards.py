from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def language_keyboard() -> InlineKeyboardMarkup:
    """
    Создаёт inline клавиатуру для выбора языка.
    Кнопки: 🇷🇺 Русский, 🇺🇿 O'zbekcha, 🇬🇧 English
    Callback data: lang_ru, lang_uz, lang_en
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="🇷🇺 Русский", callback_data="lang_ru")
    builder.button(text="🇺🇿 O'zbekcha", callback_data="lang_uz")
    builder.button(text="🇬🇧 English", callback_data="lang_en")

    builder.adjust(3)  # Расположить в одну строку

    return builder.as_markup()