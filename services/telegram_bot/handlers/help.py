from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database.models import User, InstagramAccount
from ..i18n_helper import get_text

router = Router()


@router.message(F.text == '/help')
async def handle_help(message: Message, session: AsyncSession):
    """
    Обработчик команды /help.
    Отправляет помощь на языке пользователя и подставляет имя Instagram аккаунта.
    """
    stmt = select(User).where(User.telegram_chat_id == message.from_user.id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        await message.reply("Пользователь не найден. Выполните привязку сначала.")
        return

    language = user.language or 'ru'

    stmt = select(InstagramAccount.username).where(InstagramAccount.is_primary == True)
    result = await session.execute(stmt)
    primary_account = result.scalar_one_or_none() or 'N/A'

    text = get_text('help', language, account=primary_account)
    await message.reply(text)
