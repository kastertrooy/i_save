from shared.logger import get_logger
from notifier import send_to_admin

logger = get_logger('notification_service')


async def alert_service_down(service_name: str) -> None:
    text = f"🔴 Сервис {service_name} не отвечает"
    logger.warning(text)
    await send_to_admin(text)


async def alert_queue_overflow(count: int) -> None:
    text = f"🟡 Очередь переполнена: {count} записей"
    logger.warning(text)
    await send_to_admin(text)


async def alert_account_blocked(username: str) -> None:
    text = f"🔴 Instagram аккаунт @{username} заблокирован"
    logger.warning(text)
    await send_to_admin(text)


async def alert_download_failed(url: str, error: str) -> None:
    text = f"🔴 Ошибка скачивания после 3 попыток: {url} — {error}"
    logger.warning(text)
    await send_to_admin(text)
