import json
from sqlalchemy import select, update
from shared.database.connection import async_session
from shared.database.models import InstagramAccount
from shared.encryption import decrypt, encrypt


async def load_cookies(account_id: int) -> list:
    async with async_session() as session:
        stmt = select(InstagramAccount.cookies).where(InstagramAccount.id == account_id)
        result = await session.execute(stmt)
        cookies_encrypted = result.scalar_one_or_none()

    if not cookies_encrypted:
        return []

    try:
        cookies_json = decrypt(cookies_encrypted)
        return json.loads(cookies_json)
    except (ValueError, json.JSONDecodeError):
        return []


async def save_cookies(account_id: int, cookies: list) -> None:
    cookies_json = json.dumps(cookies)
    cookies_encrypted = encrypt(cookies_json)

    async with async_session() as session:
        stmt = update(InstagramAccount).where(InstagramAccount.id == account_id).values(cookies=cookies_encrypted)
        await session.execute(stmt)
        await session.commit()


async def load_session(account_id: int) -> dict:
    async with async_session() as session:
        stmt = select(InstagramAccount.session_data).where(InstagramAccount.id == account_id)
        result = await session.execute(stmt)
        session_encrypted = result.scalar_one_or_none()

    if not session_encrypted:
        return {}

    try:
        session_json = decrypt(session_encrypted)
        return json.loads(session_json)
    except (ValueError, json.JSONDecodeError):
        return {}


async def save_session(account_id: int, session_data: dict) -> None:
    session_json = json.dumps(session_data)
    session_encrypted = encrypt(session_json)

    async with async_session() as session:
        stmt = update(InstagramAccount).where(InstagramAccount.id == account_id).values(session_data=session_encrypted)
        await session.execute(stmt)
        await session.commit()
