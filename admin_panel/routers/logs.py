from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import NotificationLog, DeliveryLog, User
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/logs', tags=['logs'])


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
    """Get error logs with optional filters."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    # In a real implementation, you would query from a dedicated logs table
    # For now, we'll return a structured response showing the filter capabilities
    logger.info('Admin %s requested error logs (service=%s, level=%s, days=%s)', 
                staff_id, service, level, days)
    
    # Placeholder logs structure - in production, would query actual log storage
    return {
        'total': 0,
        'skip': skip,
        'limit': limit,
        'filters': {
            'service': service,
            'level': level,
            'days': days
        },
        'items': []
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
    
    # Apply filters
    if user_id:
        stmt = stmt.where(DeliveryLog.user_id == user_id)
    if status_filter:
        stmt = stmt.where(DeliveryLog.status == status_filter)
    
    # Count total
    count_result = await db.execute(select(func.count()).select_from(DeliveryLog))
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
            }
            for log in logs
        ]
    }
