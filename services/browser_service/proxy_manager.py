import asyncio
from urllib.error import URLError, HTTPError
from urllib.request import Request, build_opener, ProxyHandler, ProxyBasicAuthHandler, HTTPPasswordMgrWithDefaultRealm

from sqlalchemy import select, update
from shared.database.connection import async_session
from shared.database.models import InstagramAccount, Proxy


def _build_proxy_dict(proxy_row: tuple) -> dict:
    host, port, username, password, protocol = proxy_row
    scheme = protocol.lower() if protocol else 'http'
    server = f"{scheme}://{host}:{port}"
    return {
        "server": server,
        "username": username,
        "password": password,
    }


def _fetch_ip_through_proxy(proxy: dict) -> str:
    proxy_url = proxy["server"]
    handlers = [ProxyHandler({"http": proxy_url, "https": proxy_url})]

    if proxy.get("username") and proxy.get("password"):
        password_mgr = HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, proxy_url, proxy["username"], proxy["password"])
        handlers.append(ProxyBasicAuthHandler(password_mgr))

    opener = build_opener(*handlers)
    request = Request("https://api.ipify.org", headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=10) as response:
        return response.read().decode().strip()


async def get_proxy_for_account(account_id: int) -> dict | None:
    async with async_session() as session:
        stmt = select(InstagramAccount.proxy_id).where(InstagramAccount.id == account_id)
        result = await session.execute(stmt)
        proxy_id = result.scalar_one_or_none()

    if proxy_id is None:
        return None

    async with async_session() as session:
        stmt = select(
            Proxy.host,
            Proxy.port,
            Proxy.username,
            Proxy.password,
            Proxy.protocol,
        ).where(Proxy.id == proxy_id)
        result = await session.execute(stmt)
        proxy_row = result.one_or_none()

    if not proxy_row:
        return None

    return _build_proxy_dict(proxy_row)


async def check_proxy(proxy_id: int) -> bool:
    async with async_session() as session:
        stmt = select(
            Proxy.host,
            Proxy.port,
            Proxy.username,
            Proxy.password,
            Proxy.protocol,
        ).where(Proxy.id == proxy_id)
        result = await session.execute(stmt)
        proxy_row = result.one_or_none()

    if not proxy_row:
        return False

    proxy = _build_proxy_dict(proxy_row)

    try:
        await asyncio.to_thread(_fetch_ip_through_proxy, proxy)
        is_working = True
    except (URLError, HTTPError, TimeoutError):
        is_working = False
    except Exception:
        is_working = False

    async with async_session() as session:
        stmt = update(Proxy).where(Proxy.id == proxy_id).values(is_working=is_working)
        await session.execute(stmt)
        await session.commit()

    return is_working
