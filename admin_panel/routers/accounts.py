from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import InstagramAccount, Proxy, StaffActionLog
from shared.encryption import encrypt
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/accounts', tags=['accounts'])


class InstagramAccountRequest(BaseModel):
    username: str
    password: str
    proxy_id: Optional[int] = None
    is_primary: bool = False


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
async def list_accounts(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all Instagram accounts."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(InstagramAccount)
    result = await db.execute(stmt)
    accounts = result.scalars().all()
    
    logger.info('Admin %s listed Instagram accounts', staff_id)
    
    return [
        {
            'id': a.id,
            'username': a.username,
            'proxy_id': a.proxy_id,
            'status': a.status,
            'notify_users_on_block': a.notify_users_on_block,
            'is_primary': a.is_primary,
        }
        for a in accounts
    ]


@router.post('/')
async def create_account(
    account: InstagramAccountRequest,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Create new Instagram account."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    # Check if account already exists
    stmt = select(InstagramAccount).where(InstagramAccount.username == account.username)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Account already exists'
        )
    
    # Encrypt password
    encrypted_password = encrypt(account.password)
    
    # Create account
    new_account = InstagramAccount(
        username=account.username,
        password=encrypted_password,
        proxy_id=account.proxy_id,
        status='active',
        is_primary=account.is_primary,
        notify_users_on_block=False
    )
    db.add(new_account)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='create_instagram_account',
        target_type='account',
        new_value=account.username
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s created Instagram account %s', staff_id, account.username)
    
    return {
        'id': new_account.id,
        'username': new_account.username,
        'status': 'active'
    }


@router.patch('/{account_id}')
async def update_account(
    account_id: int,
    account: InstagramAccountRequest,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Update Instagram account."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(InstagramAccount).where(InstagramAccount.id == account_id)
    result = await db.execute(stmt)
    existing_account = result.scalar_one_or_none()
    
    if not existing_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Account not found'
        )
    
    old_username = existing_account.username
    
    # Update fields
    existing_account.username = account.username
    existing_account.password = encrypt(account.password)
    existing_account.proxy_id = account.proxy_id
    existing_account.is_primary = account.is_primary
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='update_instagram_account',
        target_type='account',
        old_value=old_username,
        new_value=account.username
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s updated Instagram account %s', staff_id, account_id)
    
    return {
        'id': existing_account.id,
        'username': existing_account.username,
        'status': existing_account.status
    }


@router.delete('/{account_id}')
async def delete_account(
    account_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Delete Instagram account."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(InstagramAccount).where(InstagramAccount.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Account not found'
        )
    
    username = account.username
    await db.delete(account)
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='delete_instagram_account',
        target_type='account',
        old_value=username
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s deleted Instagram account %s', staff_id, account_id)
    
    return {'message': 'Account deleted'}


@router.post('/{account_id}/proxy')
async def set_proxy(
    account_id: int,
    proxy_id: int = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Assign proxy to account."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    # Check account exists
    stmt = select(InstagramAccount).where(InstagramAccount.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Account not found'
        )
    
    # Check proxy exists
    proxy_stmt = select(Proxy).where(Proxy.id == proxy_id)
    proxy_result = await db.execute(proxy_stmt)
    proxy = proxy_result.scalar_one_or_none()
    
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Proxy not found'
        )
    
    old_proxy_id = account.proxy_id
    account.proxy_id = proxy_id
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='assign_proxy',
        target_type='account',
        old_value=str(old_proxy_id),
        new_value=str(proxy_id)
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s assigned proxy %s to account %s', staff_id, proxy_id, account_id)
    
    return {
        'account_id': account_id,
        'proxy_id': proxy_id,
        'status': 'assigned'
    }


@router.patch('/{account_id}/notify')
async def toggle_notify(
    account_id: int,
    notify: bool = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Toggle notify_users_on_block flag."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(InstagramAccount).where(InstagramAccount.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Account not found'
        )
    
    old_notify = account.notify_users_on_block
    account.notify_users_on_block = notify
    
    # Log action
    action_log = StaffActionLog(
        staff_id=staff_id,
        action='toggle_notify',
        target_type='account',
        old_value=str(old_notify),
        new_value=str(notify)
    )
    db.add(action_log)
    
    await db.commit()
    logger.info('Admin %s toggled notify for account %s: %s', staff_id, account_id, notify)
    
    return {
        'account_id': account_id,
        'notify_users_on_block': notify
    }
