from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import User, SubscriptionLog, StaffActionLog
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/users', tags=['users'])


def _require_admin_or_staff(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None)
) -> tuple[int, str]:
    """Require admin or staff role."""
    staff_id, role = get_current_user(authorization, refresh_token)
    if role not in ['admin', 'staff']:
        logger.warning('Unauthorized user %s attempted user management', staff_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin or staff access required'
        )
    return (staff_id, role)


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
async def list_users(
    status: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """List all users with optional filters."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User)
    
    # Apply filters
    if status:
        stmt = stmt.where(User.subscription_status == status)
    if language:
        stmt = stmt.where(User.language == language)
    if search:
        stmt = stmt.where(
            (User.instagram_id.ilike(f'%{search}%')) |
            (User.telegram_username.ilike(f'%{search}%'))
        )
    
    # Count total
    count_stmt = select(func.count()).select_from(User)
    if status:
        count_stmt = count_stmt.where(User.subscription_status == status)
    if language:
        count_stmt = count_stmt.where(User.language == language)
    if search:
        count_stmt = count_stmt.where(
            (User.instagram_id.ilike(f'%{search}%')) |
            (User.telegram_username.ilike(f'%{search}%'))
        )
    
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0
    
    # Apply pagination
    stmt = stmt.offset(skip).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    logger.info('Admin/staff %s listed %s users', staff_id, len(users))
    
    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': [
            {
                'id': u.id,
                'instagram_id': u.instagram_id,
                'telegram_chat_id': u.telegram_chat_id,
                'telegram_username': u.telegram_username,
                'language': u.language,
                'subscription_status': u.subscription_status,
                'subscription_until': u.subscription_until.isoformat() if u.subscription_until else None,
                'daily_limit': u.daily_limit,
                'daily_downloads_today': u.daily_downloads_today,
            }
            for u in users
        ]
    }


@router.get('/{user_id}')
async def get_user(
    user_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get user details."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    logger.info('Admin/staff %s viewed user %s', staff_id, user_id)
    
    return {
        'id': user.id,
        'instagram_id': user.instagram_id,
        'telegram_chat_id': user.telegram_chat_id,
        'telegram_username': user.telegram_username,
        'language': user.language,
        'subscription_status': user.subscription_status,
        'subscription_until': user.subscription_until.isoformat() if user.subscription_until else None,
        'daily_limit': user.daily_limit,
        'daily_downloads_today': user.daily_downloads_today,
        'free_trial_used': user.free_trial_used,
    }


@router.post('/{user_id}/subscription')
async def grant_subscription(
    user_id: int,
    days: int = Query(..., ge=1, le=3650),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Grant subscription to a user."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    # Calculate new subscription end date
    now = datetime.utcnow()
    if user.subscription_until and user.subscription_until > now:
        new_until = user.subscription_until + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)
    
    user.subscription_until = new_until
    user.subscription_status = 'active'
    
    # Log subscription
    sub_log = SubscriptionLog(
        user_id=user_id,
        action='granted',
        granted_by=f'staff_{staff_id}',
        period_days=days
    )
    db.add(sub_log)
    
    # Log staff action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='grant_subscription',
        target_type='user',
        old_value=str(user.subscription_until),
        new_value=str(new_until)
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Staff %s granted %s days subscription to user %s', staff_id, days, user_id)
    
    return {
        'user_id': user_id,
        'subscription_until': new_until.isoformat(),
        'subscription_status': 'active'
    }


@router.post('/subscription/bulk')
async def bulk_grant_subscription(
    days: int = Query(..., ge=1, le=3650),
    user_ids: Optional[list[int]] = None,
    all_users: bool = Query(False),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Grant subscription to multiple users."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    if not all_users and not user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provide either all_users=true or user_ids list'
        )
    
    # Get users to update
    if all_users:
        stmt = select(User)
    else:
        stmt = select(User).where(User.id.in_(user_ids))
    
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    now = datetime.utcnow()
    updated_count = 0
    
    for user in users:
        if user.subscription_until and user.subscription_until > now:
            new_until = user.subscription_until + timedelta(days=days)
        else:
            new_until = now + timedelta(days=days)
        
        user.subscription_until = new_until
        user.subscription_status = 'active'
        
        # Log subscription
        sub_log = SubscriptionLog(
            user_id=user.id,
            action='granted_bulk',
            granted_by=f'staff_{staff_id}',
            period_days=days
        )
        db.add(sub_log)
        updated_count += 1
    
    # Log staff action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='bulk_grant_subscription',
        target_type='users',
        new_value=f'{updated_count} users, {days} days'
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Staff %s bulk-granted %s days to %s users', staff_id, days, updated_count)
    
    return {'updated_count': updated_count, 'days': days}


@router.post('/{user_id}/block')
async def block_user(
    user_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Block a user."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    old_status = user.subscription_status
    user.subscription_status = 'blocked'
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='block_user',
        target_type='user',
        old_value=old_status,
        new_value='blocked'
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Staff %s blocked user %s', staff_id, user_id)
    
    return {'user_id': user_id, 'status': 'blocked'}


@router.post('/{user_id}/unblock')
async def unblock_user(
    user_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Unblock a user."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    old_status = user.subscription_status
    user.subscription_status = 'active'
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='unblock_user',
        target_type='user',
        old_value=old_status,
        new_value='active'
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Staff %s unblocked user %s', staff_id, user_id)
    
    return {'user_id': user_id, 'status': 'active'}


@router.patch('/{user_id}/daily-limit')
async def update_daily_limit(
    user_id: int,
    new_limit: int = Query(..., ge=1, le=10000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Update user daily limit."""
    staff_id, role = _require_admin_or_staff(authorization, refresh_token)
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    old_limit = user.daily_limit
    user.daily_limit = new_limit
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='update_daily_limit',
        target_type='user',
        old_value=str(old_limit),
        new_value=str(new_limit)
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Staff %s updated daily limit for user %s: %s -> %s', staff_id, user_id, old_limit, new_limit)
    
    return {'user_id': user_id, 'daily_limit': new_limit}
