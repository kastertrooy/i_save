from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import StaffAccount, StaffActionLog
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/staff', tags=['staff'])


class StaffRequest(BaseModel):
    username: str
    password: str
    role: str  # admin or staff


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
async def list_staff(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all staff members."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(StaffAccount)
    result = await db.execute(stmt)
    staff = result.scalars().all()
    
    logger.info('Admin %s listed staff members', staff_id)
    
    return [
        {
            'id': s.id,
            'username': s.username,
            'role': s.role,
        }
        for s in staff
    ]


@router.post('/')
async def create_staff(
    staff_data: StaffRequest,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Create new staff member."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    # Validate role
    if staff_data.role not in ['admin', 'staff']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Role must be admin or staff'
        )
    
    # Check if username already exists
    stmt = select(StaffAccount).where(StaffAccount.username == staff_data.username)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Username already exists'
        )
    
    # Hash password
    password_hash = bcrypt.hashpw(
        staff_data.password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')
    
    # Create staff member
    new_staff = StaffAccount(
        username=staff_data.username,
        password_hash=password_hash,
        role=staff_data.role
    )
    db.add(new_staff)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='create_staff',
        target_type='staff',
        new_value=f'{staff_data.username} ({staff_data.role})'
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s created staff member %s with role %s', staff_id, staff_data.username, staff_data.role)
    
    return {
        'id': new_staff.id,
        'username': new_staff.username,
        'role': new_staff.role,
    }


@router.patch('/{staff_member_id}/role')
async def update_staff_role(
    staff_member_id: int,
    role: str = Query(..., regex='^(admin|staff)$'),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Update staff member role."""
    admin_id, admin_role = _require_admin(authorization, refresh_token)
    
    # Prevent self-demotion
    if staff_member_id == admin_id and role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot remove admin role from yourself'
        )
    
    stmt = select(StaffAccount).where(StaffAccount.id == staff_member_id)
    result = await db.execute(stmt)
    staff = result.scalar_one_or_none()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Staff member not found'
        )
    
    old_role = staff.role
    staff.role = role
    
    # Log action
    action_log = StaffActionLog(
        staff_id=admin_id,
        action='update_staff_role',
        target_type='staff',
        old_value=old_role,
        new_value=role
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s changed role for staff %s: %s -> %s', admin_id, staff_member_id, old_role, role)
    
    return {
        'id': staff.id,
        'username': staff.username,
        'role': staff.role,
    }


@router.delete('/{staff_member_id}')
async def delete_staff(
    staff_member_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Delete staff member."""
    admin_id, admin_role = _require_admin(authorization, refresh_token)
    
    # Prevent self-deletion
    if staff_member_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot delete yourself'
        )
    
    stmt = select(StaffAccount).where(StaffAccount.id == staff_member_id)
    result = await db.execute(stmt)
    staff = result.scalar_one_or_none()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Staff member not found'
        )
    
    username = staff.username
    await db.delete(staff)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=admin_id,
        action='delete_staff',
        target_type='staff',
        old_value=username
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s deleted staff member %s', admin_id, staff_member_id)
    
    return {'message': 'Staff member deleted'}


@router.get('/logs')
async def get_action_logs(
    staff_id_filter: Optional[int] = Query(None),
    target_type: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get staff action logs with optional filters."""
    admin_id, admin_role = _require_admin(authorization, refresh_token)
    
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    
    stmt = select(StaffActionLog)
    
    # Apply filters
    if staff_id_filter:
        stmt = stmt.where(StaffActionLog.staff_id == staff_id_filter)
    if target_type:
        stmt = stmt.where(StaffActionLog.target_type == target_type)
    
    # Count total matching logs
    count_result = await db.execute(select(func.count()).select_from(StaffActionLog))
    total = count_result.scalar() or 0
    
    # Apply pagination and ordering
    stmt = stmt.order_by(StaffActionLog.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    
    logger.info('Admin %s retrieved action logs (skip=%s, limit=%s)', admin_id, skip, limit)
    
    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': [
            {
                'id': log.id,
                'staff_id': log.staff_id,
                'action': log.action,
                'target_type': log.target_type,
                'old_value': log.old_value,
                'new_value': log.new_value,
            }
            for log in logs
        ]
    }
