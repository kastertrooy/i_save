import asyncio
import os
import socket
from contextlib import suppress
from datetime import datetime
from typing import Optional

from sqlalchemy import insert, select, update

from shared.database.connection import async_session
from shared.database.models import ServiceInstance
from shared.logger import get_logger

logger = get_logger('service_heartbeat')

DEFAULT_INTERVAL_SECONDS = 60


def get_instance_name(default: str) -> str:
    return os.getenv('SERVICE_INSTANCE_NAME') or os.getenv('INSTANCE_NAME') or default


def get_container_id() -> str:
    return os.getenv('HOSTNAME') or socket.gethostname()


async def update_service_heartbeat(
    service_type: str,
    instance_name: str,
    status: str = ServiceInstance.STATUS_RUNNING,
    container_id: Optional[str] = None,
    queue_start_position: Optional[int] = None,
) -> None:
    now = datetime.utcnow()
    resolved_container_id = container_id or get_container_id()

    async with async_session() as session:
        stmt = select(ServiceInstance).where(
            ServiceInstance.service_type == service_type,
            ServiceInstance.instance_name == instance_name,
        )
        result = await session.execute(stmt)
        instance = result.scalar_one_or_none()

        values = {
            'status': status,
            'container_id': resolved_container_id,
            'last_heartbeat_at': now,
        }
        if queue_start_position is not None:
            values['queue_start_position'] = queue_start_position

        if instance:
            if instance.started_at is None or instance.status == ServiceInstance.STATUS_STOPPED:
                values['started_at'] = now
            await session.execute(
                update(ServiceInstance)
                .where(ServiceInstance.id == instance.id)
                .values(**values)
            )
        else:
            await session.execute(
                insert(ServiceInstance).values(
                    service_type=service_type,
                    instance_name=instance_name,
                    started_at=now,
                    **values,
                )
            )
        await session.commit()


async def mark_service_stopped(service_type: str, instance_name: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(ServiceInstance)
            .where(
                ServiceInstance.service_type == service_type,
                ServiceInstance.instance_name == instance_name,
            )
            .values(status=ServiceInstance.STATUS_STOPPED, last_heartbeat_at=datetime.utcnow())
        )
        await session.commit()


async def heartbeat_loop(
    service_type: str,
    instance_name: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    queue_start_position: Optional[int] = None,
) -> None:
    while True:
        try:
            await update_service_heartbeat(
                service_type=service_type,
                instance_name=instance_name,
                queue_start_position=queue_start_position,
            )
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception('Heartbeat failed for %s/%s: %s', service_type, instance_name, exc)
            await asyncio.sleep(interval_seconds)


def start_heartbeat_task(
    service_type: str,
    instance_name: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    queue_start_position: Optional[int] = None,
) -> asyncio.Task:
    return asyncio.create_task(
        heartbeat_loop(
            service_type=service_type,
            instance_name=instance_name,
            interval_seconds=interval_seconds,
            queue_start_position=queue_start_position,
        )
    )


async def stop_heartbeat_task(task: Optional[asyncio.Task], service_type: str, instance_name: str) -> None:
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await mark_service_stopped(service_type, instance_name)
