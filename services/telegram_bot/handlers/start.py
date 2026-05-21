from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import ContentQueue, SubscriptionLog, SystemSetting, User
from ..i18n_helper import get_text
from ..keyboards import language_keyboard

router = Router()


@router.message(F.text == '/start')
async def handle_start(message: Message, session: AsyncSession):
    text = get_text('welcome', 'ru')
    await message.reply(text)


@router.message(F.text.regexp(r'/start (.+)'))
async def handle_start_with_token(message: Message, session: AsyncSession):
    token = message.text.split(maxsplit=1)[1].strip()

    stmt = select(User).where(User.bind_token == token, User.bind_token_expires_at > func.now())
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        text = get_text('bind_invalid', 'ru')
        await message.reply(text)
        return

    activate_queue_stmt = (
        update(ContentQueue)
        .where(
            ContentQueue.instagram_id == user.instagram_id,
            ContentQueue.status == 'no_telegram',
        )
        .values(status='pending')
    )

    if user.telegram_chat_id is not None:
        await session.execute(activate_queue_stmt)
        await session.commit()
        await message.reply('Аккаунт уже привязан')
        return

    stmt = (
        update(User)
        .where(User.id == user.id)
        .values(
            telegram_chat_id=message.from_user.id,
            telegram_username=message.from_user.username,
            bind_token=None,
            bind_token_expires_at=None,
        )
    )
    await session.execute(stmt)

    if not user.free_trial_used:
        stmt = select(SystemSetting.value).where(SystemSetting.key == 'free_trial_days')
        result = await session.execute(stmt)
        free_trial_days = int(result.scalar_one_or_none() or 30)
        subscription_until = datetime.utcnow() + timedelta(days=free_trial_days)

        stmt = (
            update(User)
            .where(User.id == user.id)
            .values(
                subscription_status='free_trial',
                subscription_until=subscription_until,
                free_trial_used=True,
            )
        )
        await session.execute(stmt)

        stmt = insert(SubscriptionLog).values(
            user_id=user.id,
            action='free_trial',
            granted_by='system',
            period_days=free_trial_days,
        )
        await session.execute(stmt)

    await session.execute(activate_queue_stmt)
    await session.commit()

    text = get_text('language_select', 'ru')
    await message.reply(text, reply_markup=language_keyboard())
