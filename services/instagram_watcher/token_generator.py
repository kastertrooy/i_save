import uuid
from datetime import datetime, timedelta

from shared.config import settings
from shared.database.connection import async_session
from shared.database.models import User
from sqlalchemy import update


async def generate_bind_token(instagram_id: str) -> str:
    token = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=24)

    async with async_session() as session:
        stmt = update(User).where(User.instagram_id == instagram_id).values(
            bind_token=token,
            bind_token_expires_at=expires_at,
        )
        await session.execute(stmt)
        await session.commit()

    return token


def get_bind_link(token: str) -> str:
    return f"t.me/{settings.telegram_bot_username}?start={token}"
