from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import TelegramStorageGroup, StaffActionLog
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/groups', tags=['groups'])


class TelegramGroupRequest(BaseModel):
    name: str
    telegram_group_id: int


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
async def list_groups(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all Telegram storage groups."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(TelegramStorageGroup)
    result = await db.execute(stmt)
    groups = result.scalars().all()
    
    logger.info('Admin %s listed Telegram storage groups', staff_id)
    
    return [
        {
            'id': g.id,
            'name': g.name,
            'telegram_group_id': g.telegram_group_id,
        }
        for g in groups
    ]


@router.post('/')
async def create_group(
    group: TelegramGroupRequest,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Create new Telegram storage group."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    # Check if group already exists
    stmt = select(TelegramStorageGroup).where(
        TelegramStorageGroup.telegram_group_id == group.telegram_group_id
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Group already exists'
        )
    
    # Create group
    new_group = TelegramStorageGroup(
        name=group.name,
        telegram_group_id=group.telegram_group_id
    )
    db.add(new_group)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='create_storage_group',
        target_type='group',
        new_value=group.name
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s created storage group %s', staff_id, group.name)
    
    return {
        'id': new_group.id,
        'name': new_group.name,
        'telegram_group_id': new_group.telegram_group_id,
    }


@router.delete('/{group_id}')
async def delete_group(
    group_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Delete Telegram storage group."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(TelegramStorageGroup).where(TelegramStorageGroup.id == group_id)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()
    
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Group not found'
        )
    
    group_name = group.name
    await db.delete(group)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='delete_storage_group',
        target_type='group',
        old_value=group_name
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s deleted storage group %s', staff_id, group_id)
    
    return {'message': 'Group deleted'}


@router.patch('/{group_id}/status')
async def update_group_status(
    group_id: int,
    enabled: bool = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Enable/disable a storage group."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(TelegramStorageGroup).where(TelegramStorageGroup.id == group_id)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()
    
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Group not found'
        )
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='toggle_storage_group_status',
        target_type='group',
        new_value='enabled' if enabled else 'disabled'
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s toggled storage group %s status to %s', staff_id, group_id, enabled)
    
    return {
        'id': group.id,
        'name': group.name,
        'enabled': enabled
    }
