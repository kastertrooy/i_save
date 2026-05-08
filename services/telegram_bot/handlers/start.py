from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert, func
from datetime import timedelta

from shared.database.models import User, ContentQueue, SubscriptionLog, SystemSetting
from ..i18n_helper import get_text
from ..keyboards import language_keyboard

router = Router()


@router.message(F.text == '/start')
async def handle_start(message: Message, session: AsyncSession):
    """
    Обработчик команды /start без параметров.
    Отправляет приветствие на русском (язык по умолчанию).
    """
    text = get_text('welcome', 'ru')
    await message.reply(text)


@router.message(F.text.regexp(r'/start (.+)'))
async def handle_start_with_token(message: Message, session: AsyncSession):
    """
    Обработчик команды /start с bind_token.
    Выполняет привязку аккаунта, настраивает подписку, показывает выбор языка,
    активирует очередь контента.
    """
    token = message.text.split()[1]

    # 1. Найти пользователя по токену
    stmt = select(User).where(User.bind_token == token, User.bind_token_expires_at > func.now())
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        text = get_text('bind_invalid', 'ru')
        await message.reply(text)
        return

    if user.telegram_chat_id is not None:
        await message.reply("Аккаунт уже привязан")
        return

    # 4. Обновить пользователя
    stmt = update(User).where(User.id == user.id).values(
        telegram_chat_id=message.from_user.id,
        telegram_username=message.from_user.username,
        bind_token=None,
        bind_token_expires_at=None
    )
    await session.execute(stmt)

    # 5. Настроить free trial если не использован
    if not user.free_trial_used:
        # Получить free_trial_days из system_settings
        stmt = select(SystemSetting.value).where(SystemSetting.key == 'free_trial_days')
        result = await session.execute(stmt)
        free_trial_days = int(result.scalar_one_or_none() or 30)

        subscription_until = func.now() + timedelta(days=free_trial_days)

        stmt = update(User).where(User.id == user.id).values(
            subscription_status='free_trial',
            subscription_until=subscription_until,
            free_trial_used=True
        )
        await session.execute(stmt)

        # Лог подписки
        stmt = insert(SubscriptionLog).values(
            user_id=user.id,
            action='free_trial',
            granted_by='system',
            period_days=free_trial_days
        )
        await session.execute(stmt)

    await session.commit()

    # 6. Показать клавиатуру выбора языка
    text = get_text('language_select', 'ru')
    await message.reply(text, reply_markup=language_keyboard())

    # 7. Активировать очередь контента
    stmt = update(ContentQueue).where(
        ContentQueue.instagram_id == user.instagram_id,
        ContentQueue.status == 'no_telegram'
    ).values(status='pending')
    await session.execute(stmt)

    await session.commit()