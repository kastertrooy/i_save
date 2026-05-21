from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from shared.database.models import User
from ..i18n_helper import get_text

router = Router()


@router.callback_query(F.data.in_(["lang_ru", "lang_uz", "lang_en"]))
async def handle_language_selection(callback: CallbackQuery, session: AsyncSession):
    """
    Обработчик выбора языка.
    Обновляет язык пользователя в БД, удаляет сообщение с кнопками,
    отправляет приветствие на выбранном языке.
    """
    language = callback.data.split('_')[1]  # Извлекаем 'ru', 'uz' или 'en'

    # Обновляем язык пользователя в БД
    stmt = update(User).where(User.telegram_chat_id == callback.from_user.id).values(language=language)
    await session.execute(stmt)
    await session.commit()

    # Удаляем сообщение с кнопками выбора языка
    await callback.message.delete()

    # Отправляем приветствие на выбранном языке
    text = get_text('welcome', language)
    await callback.bot.send_message(chat_id=callback.from_user.id, text=text)