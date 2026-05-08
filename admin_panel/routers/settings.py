from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import SystemSetting, StaffActionLog
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/settings', tags=['settings'])


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
async def get_all_settings(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get all system settings."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(SystemSetting)
    result = await db.execute(stmt)
    settings = result.scalars().all()
    
    logger.info('Admin %s retrieved all system settings', staff_id)
    
    return {
        'settings': [
            {
                'key': s.key,
                'value': s.value,
            }
            for s in settings
        ]
    }


@router.get('/{key}')
async def get_setting(
    key: str,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get specific setting."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()
    
    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Setting not found'
        )
    
    logger.info('Admin %s retrieved setting %s', staff_id, key)
    
    return {'key': setting.key, 'value': setting.value}


@router.patch('/{key}')
async def update_setting(
    key: str,
    value: str = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Update system setting."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()
    
    if not setting:
        # Create new setting if it doesn't exist
        setting = SystemSetting(key=key, value=value)
        db.add(setting)
    else:
        old_value = setting.value
        setting.value = value
        
        # Log action
        action_log = StaffActionLog(
            staff_id=staff_id,
            action='update_system_setting',
            target_type='setting',
            old_value=old_value,
            new_value=value
        )
        db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s updated setting %s to %s', staff_id, key, value)
    
    return {'key': key, 'value': value}


@router.delete('/{key}')
async def delete_setting(
    key: str,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Delete system setting."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()
    
    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Setting not found'
        )
    
    await db.delete(setting)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='delete_system_setting',
        target_type='setting',
        old_value=setting.value
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s deleted setting %s', staff_id, key)
    
    return {'message': 'Setting deleted'}
