from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, status, Request, Response, Cookie, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import StaffAccount
from shared.logger import get_logger
from admin_panel.middleware.brute_force import (
    check_brute_force, register_failed_attempt, reset_attempts
)
from admin_panel.middleware.auth_middleware import (
    create_access_token, create_refresh_token, verify_token, get_current_user
)

logger = get_logger('admin_panel')
router = APIRouter(prefix='/auth', tags=['auth'])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'


class MeResponse(BaseModel):
    id: int
    username: str
    role: str


@router.post('/login', response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
) -> LoginResponse:
    """Login with username and password."""
    client_ip = http_request.client.host
    
    # Check brute force protection
    block_remaining = await check_brute_force(client_ip, db)
    if block_remaining is not None:
        logger.warning('Login attempt from blocked IP %s, %s seconds remaining', client_ip, block_remaining)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f'Too many attempts. Try again in {block_remaining} seconds.'
        )
    
    # Find staff account
    stmt = select(StaffAccount).where(StaffAccount.username == request.username)
    result = await db.execute(stmt)
    staff_account = result.scalar_one_or_none()
    
    if not staff_account:
        # Register failed attempt
        block_duration = await register_failed_attempt(client_ip, request.username, db)
        logger.warning(
            'Login failed: username %s not found from IP %s',
            request.username, client_ip
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid credentials'
        )
    
    # Check password
    password_valid = bcrypt.checkpw(
        request.password.encode('utf-8'),
        staff_account.password_hash.encode('utf-8')
    )
    
    if not password_valid:
        # Register failed attempt
        block_duration = await register_failed_attempt(client_ip, request.username, db)
        logger.warning(
            'Login failed: invalid password for username %s from IP %s',
            request.username, client_ip
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid credentials'
        )
    
    # Reset brute force attempts on successful login
    await reset_attempts(client_ip, db)
    logger.info('Successful login for user %s from IP %s', request.username, client_ip)
    
    # Create tokens
    access_token = create_access_token(staff_account.id, staff_account.role)
    refresh_token = create_refresh_token(staff_account.id, staff_account.role)
    
    # Set refresh token as httpOnly cookie
    response.set_cookie(
        key='refresh_token',
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite='strict',
        max_age=7 * 24 * 60 * 60  # 7 days
    )
    
    return LoginResponse(access_token=access_token)


@router.post('/logout')
async def logout(response: Response) -> dict:
    """Logout by removing refresh token cookie."""
    response.delete_cookie(key='refresh_token')
    logger.info('User logged out')
    return {'message': 'Logged out successfully'}


@router.post('/refresh', response_model=LoginResponse)
async def refresh(refresh_token: Optional[str] = Cookie(None)) -> LoginResponse:
    """Refresh access token using refresh token."""
    if not refresh_token:
        logger.warning('Refresh endpoint called without refresh token')
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Refresh token required'
        )
    
    # Verify refresh token
    payload = verify_token(refresh_token, token_type='refresh')
    
    # Create new access token
    access_token = create_access_token(payload.sub, payload.role)
    logger.info('Access token refreshed for staff_id %s', payload.sub)
    
    return LoginResponse(access_token=access_token)


@router.get('/me', response_model=MeResponse)
async def me(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> MeResponse:
    """Get current user info."""
    staff_id, role = get_current_user(authorization, refresh_token)
    
    stmt = select(StaffAccount).where(StaffAccount.id == staff_id)
    result = await db.execute(stmt)
    staff = result.scalar_one_or_none()
    
    if not staff:
        logger.warning('Staff account not found for ID %s', staff_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found'
        )
    
    return MeResponse(id=staff.id, username=staff.username, role=staff.role)
