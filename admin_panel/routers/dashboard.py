from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import (
    ContentQueue, User, ServiceInstance, DeliveryLog
)
from shared.logger import get_logger

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/metrics', tags=['metrics'])


@router.get('/summary')
async def get_metrics_summary(db: AsyncSession = Depends(get_db)) -> dict:
    """Get summary metrics."""
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    week_ago = today - timedelta(days=7)
    
    # Queue metrics
    pending_stmt = select(func.count()).select_from(ContentQueue).where(ContentQueue.status == 'pending')
    no_telegram_stmt = select(func.count()).select_from(ContentQueue).where(
        ContentQueue.status == 'downloaded'
    )
    failed_stmt = select(func.count()).select_from(ContentQueue).where(ContentQueue.status == 'failed')
    
    pending_result = await db.execute(pending_stmt)
    no_telegram_result = await db.execute(no_telegram_stmt)
    failed_result = await db.execute(failed_stmt)
    
    queue_pending = pending_result.scalar_one() or 0
    queue_no_telegram = no_telegram_result.scalar_one() or 0
    queue_failed = failed_result.scalar_one() or 0
    
    # Downloads today (from delivery logs)
    downloads_today_stmt = select(func.count()).select_from(DeliveryLog).where(
        DeliveryLog.delivery_type == 'media'
    )
    downloads_today_result = await db.execute(downloads_today_stmt)
    downloads_today = downloads_today_result.scalar_one() or 0
    
    # User metrics
    active_users_stmt = select(func.count(func.distinct(User.id))).select_from(User).where(
        User.subscription_status.in_(['active', 'free_trial'])
    )
    new_users_today_stmt = select(func.count()).select_from(User)
    new_users_week_stmt = select(func.count()).select_from(User)
    
    active_users_result = await db.execute(active_users_stmt)
    new_users_today_result = await db.execute(new_users_today_stmt)
    new_users_week_result = await db.execute(new_users_week_stmt)
    
    active_users = active_users_result.scalar_one() or 0
    new_users_today = new_users_today_result.scalar_one() or 0
    new_users_week = new_users_week_result.scalar_one() or 0
    
    logger.debug('Summary metrics queried')
    
    return {
        'queue_pending': queue_pending,
        'queue_no_telegram': queue_no_telegram,
        'queue_failed': queue_failed,
        'downloaded_today': downloads_today,
        'mb_downloaded_today': 0.0,  # Would need to track file sizes
        'active_users': active_users,
        'new_users_today': new_users_today,
        'new_users_week': new_users_week,
    }


@router.get('/chart')
async def get_metrics_chart(
    period: str = 'auto',
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get metrics for chart visualization."""
    if period not in ['7d', '30d', '90d']:
        period = '7d'
    
    days = {
        '7d': 7,
        '30d': 30,
        '90d': 90,
    }[period]
    
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    
    # Generate labels (dates)
    labels = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= now:
        labels.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    # For simplicity, return empty data that can be filled in by frontend
    # Real implementation would aggregate by date
    downloads_data = [0] * len(labels)
    new_users_data = [0] * len(labels)
    
    logger.debug('Chart metrics generated for period %s', period)
    
    return {
        'labels': labels,
        'downloads': downloads_data,
        'new_users': new_users_data,
    }


@router.get('/services/status')
async def get_services_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Get service instances with heartbeat status."""
    now = datetime.utcnow()
    heartbeat_threshold = now - timedelta(minutes=5)
    
    stmt = select(ServiceInstance)
    result = await db.execute(stmt)
    services = result.scalars().all()
    
    services_data = []
    for service in services:
        is_alive = (
            service.last_heartbeat_at is not None and
            service.last_heartbeat_at > heartbeat_threshold
        )
        services_data.append({
            'id': service.id,
            'service_type': service.service_type,
            'instance_name': service.instance_name,
            'status': service.status,
            'is_alive': is_alive,
            'last_heartbeat_at': service.last_heartbeat_at.isoformat() if service.last_heartbeat_at else None,
        })
    
    logger.debug('Service status queried, %s services', len(services_data))
    
    return {'services': services_data}
