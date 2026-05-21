from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from shared.database.models import User
from ..i18n_helper import get_text

router = Router()


@router.message(F.text == '/status')
async def handle_status(message: Message, session: AsyncSession):
    """
    Обработчик команды /status.
    Показывает статус подписки на языке пользователя.
    """
    stmt = select(User).where(User.telegram_chat_id == message.from_user.id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        await message.reply("Пользователь не найден. Выполните привязку сначала.")
        return

    language = user.language or 'ru'
    status = user.subscription_status

    if status == 'active':
        finish_date = user.subscription_until.strftime('%Y-%m-%d') if user.subscription_until else 'N/A'
        text = get_text('status_active', language, date=finish_date)
    elif status == 'free_trial':
        if user.subscription_until:
            delta = user.subscription_until - datetime.utcnow()
            days_left = max(delta.days, 0)
        else:
            days_left = 0
        text = get_text('status_trial', language, days=days_left)
    elif status == 'expired':
        used = 0
        limit = user.daily_limit or 0
        text = get_text('status_expired', language, used=used, limit=limit)
    elif status == 'blocked':
        text = get_text('status_blocked', language)
    else:
        text = get_text('status_active', language, date='N/A')

    await message.reply(text)
