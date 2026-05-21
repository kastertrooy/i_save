from datetime import datetime, timedelta
from typing import Optional
import re

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
import docker
from docker.errors import DockerException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import NotificationLog, DeliveryLog, User
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/logs', tags=['logs'])
LOG_LINE_RE = re.compile(
    r'^\[(?P<timestamp>[^\]]+)\]\s+\[(?P<service>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<message>.*)$'
)


def _guess_level(line: str) -> str:
    upper_line = line.upper()
    if 'ERROR' in upper_line or 'EXCEPTION' in upper_line or 'TRACEBACK' in upper_line:
        return 'ERROR'
    if 'WARNING' in upper_line or 'WARN' in upper_line:
        return 'WARNING'
    if 'DEBUG' in upper_line:
        return 'DEBUG'
    return 'INFO'


def _require_admin(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None)
) -> tuple[int, str]:
    """Require admin role."""
    staff_id, role = get_current_user(authorization, refresh_token)
    if role != 'admin':
        logger.warning('Non-admin user %s attempted admin operation', staff_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required'
        )
    return (staff_id, role)


@router.get('/')
async def get_logs(
    service: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get service logs with optional filters."""
    staff_id, role = _require_admin(authorization, refresh_token)
    start_date = datetime.utcnow() - timedelta(days=days)
    normalized_level = level.upper() if level else None
    items = []

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        for container in containers:
            container_name = container.name
            if service and service.lower() not in container_name.lower():
                continue

            raw_logs = container.logs(since=start_date, tail=500).decode('utf-8', errors='replace')
            for raw_line in raw_logs.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                parsed = LOG_LINE_RE.match(line)
                if parsed:
                    log_service = parsed.group('service')
                    log_level = parsed.group('level').upper()
                    timestamp = parsed.group('timestamp')
                    message = parsed.group('message')
                else:
                    log_service = container_name
                    log_level = _guess_level(line)
                    timestamp = None
                    message = line

                if normalized_level and log_level != normalized_level:
                    continue
                if service and service.lower() not in log_service.lower() and service.lower() not in container_name.lower():
                    continue

                items.append({
                    'id': len(items) + 1,
                    'container': container_name,
                    'service': log_service,
                    'level': log_level.lower(),
                    'timestamp': timestamp,
                    'message': message,
                })
    except DockerException as exc:
        logger.error('Failed to read Docker logs: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to read Docker logs',
        )

    items.reverse()
    total = len(items)
    page_items = items[skip:skip + limit]

    logger.info(
        'Admin %s requested logs (service=%s, level=%s, days=%s, returned=%s)',
        staff_id, service, level, days, len(page_items),
    )

    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'filters': {
            'service': service,
            'level': level,
            'days': days
        },
        'items': page_items
    }


@router.get('/notifications')
async def get_notification_logs(
    notification_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get notification logs."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    
    stmt = select(NotificationLog)
    
    # Apply filters
    if notification_type:
        stmt = stmt.where(NotificationLog.notification_type == notification_type)
    if status_filter:
        stmt = stmt.where(NotificationLog.status == status_filter)
    
    # Count total
    count_result = await db.execute(select(func.count()).select_from(NotificationLog))
    total = count_result.scalar() or 0
    
    # Apply pagination
    stmt = stmt.order_by(NotificationLog.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    
    logger.info('Admin %s retrieved %s notification logs', staff_id, len(logs))
    
    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': [
            {
                'id': log.id,
                'recipient_type': log.recipient_type,
                'notification_type': log.notification_type,
                'status': log.status,
            }
            for log in logs
        ]
    }


@router.get('/deliveries')
async def get_delivery_logs(
    user_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get delivery logs with filters."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    
    stmt = select(DeliveryLog)
    stmt = stmt.where(DeliveryLog.created_at >= start_date)
    
    # Apply filters
    if user_id:
        stmt = stmt.where(DeliveryLog.user_id == user_id)
    if status_filter:
        stmt = stmt.where(DeliveryLog.status == status_filter)
    
    # Count total
    count_stmt = select(func.count()).select_from(DeliveryLog).where(DeliveryLog.created_at >= start_date)
    if user_id:
        count_stmt = count_stmt.where(DeliveryLog.user_id == user_id)
    if status_filter:
        count_stmt = count_stmt.where(DeliveryLog.status == status_filter)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0
    
    # Apply pagination
    stmt = stmt.order_by(DeliveryLog.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    
    logger.info('Admin %s retrieved %s delivery logs', staff_id, len(logs))
    
    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': [
            {
                'id': log.id,
                'user_id': log.user_id,
                'content_queue_id': log.content_queue_id,
                'delivery_type': log.delivery_type,
                'status': log.status,
                'created_at': log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    }
