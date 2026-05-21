import json
import os
from pathlib import Path

from sqlalchemy import select, update
from shared.database.connection import async_session
from shared.database.models import InstagramAccount
from shared.encryption import decrypt, encrypt

COOKIES_DIR = Path(os.getenv('COOKIES_DIR', '/app/Cookies'))


def _cookies_file(account_id: int) -> Path:
    return COOKIES_DIR / f'account_{account_id}.cookies.enc'


def _load_cookies_from_file(account_id: int) -> list:
    path = _cookies_file(account_id)
    if not path.exists():
        return []

    try:
        cookies_json = decrypt(path.read_text(encoding='utf-8'))
        cookies = json.loads(cookies_json)
        return cookies if isinstance(cookies, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _save_cookies_to_file(account_id: int, cookies_encrypted: str) -> None:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    _cookies_file(account_id).write_text(cookies_encrypted, encoding='utf-8')


async def load_cookies(account_id: int) -> list:
    cookies = _load_cookies_from_file(account_id)
    if cookies:
        return cookies

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
    _save_cookies_to_file(account_id, cookies_encrypted)

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
