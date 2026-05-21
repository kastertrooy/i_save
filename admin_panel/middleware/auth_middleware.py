from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel

from shared.config import Settings
from shared.logger import get_logger

logger = get_logger('admin_panel')


class TokenPayload(BaseModel):
    sub: int  # staff_id
    role: str  # admin or staff
    exp: datetime
    iat: datetime
    type: str  # access or refresh


def _get_settings() -> Settings:
    return Settings()


def create_access_token(staff_id: int, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token.
    
    Args:
        staff_id: Staff account ID
        role: Staff role ('admin' or 'staff')
        expires_delta: Token expiration time delta (default 15 minutes)
    
    Returns:
        Encoded JWT token
    """
    settings = _get_settings()
    
    if expires_delta is None:
        expires_delta = timedelta(minutes=15)
    
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expire = now + expires_delta
    
    payload = {
        'sub': str(staff_id),
        'role': role,
        'exp': expire,
        'iat': now,
        'type': 'access'
    }
    
    token = jwt.encode(
        payload,
        settings.secret_key,
        algorithm='HS256'
    )
    logger.debug('Access token created for staff_id %s', staff_id)
    return token


def create_refresh_token(staff_id: int, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT refresh token.
    
    Args:
        staff_id: Staff account ID
        role: Staff role ('admin' or 'staff')
        expires_delta: Token expiration time delta (default 7 days)
    
    Returns:
        Encoded JWT token
    """
    settings = _get_settings()
    
    if expires_delta is None:
        expires_delta = timedelta(days=7)
    
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expire = now + expires_delta
    
    payload = {
        'sub': str(staff_id),
        'role': role,
        'exp': expire,
        'iat': now,
        'type': 'refresh'
    }
    
    token = jwt.encode(
        payload,
        settings.secret_key,
        algorithm='HS256'
    )
    logger.debug('Refresh token created for staff_id %s', staff_id)
    return token


def verify_token(token: str, token_type: str = 'access') -> TokenPayload:
    """
    Verify and decode JWT token.
    
    Args:
        token: JWT token string
        token_type: Expected token type ('access' or 'refresh')
    
    Returns:
        TokenPayload with decoded claims
    
    Raises:
        HTTPException if token is invalid or expired
    """
    settings = _get_settings()
    
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=['HS256']
        )
        
        # Check token type
        if payload.get('type') != token_type:
            logger.warning('Token type mismatch: expected %s, got %s', token_type, payload.get('type'))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid token type'
            )
        
        staff_id = payload.get('sub')
        role = payload.get('role')
        
        if staff_id is None or role is None:
            logger.warning('Token missing required claims')
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid token claims'
            )
        
        return TokenPayload(
            sub=int(staff_id),
            role=role,
            exp=datetime.fromisoformat(payload['exp']) if isinstance(payload['exp'], str) else payload['exp'],
            iat=datetime.fromisoformat(payload['iat']) if isinstance(payload['iat'], str) else payload['iat'],
            type=payload['type']
        )
    
    except ExpiredSignatureError as e:
        logger.warning('JWT verification failed: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Token expired'
        )
    except JWTError as e:
        logger.warning('JWT verification failed: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid token'
        )


def get_current_user(authorization_header: Optional[str], refresh_token: Optional[str] = None) -> tuple[int, str]:
    """
    Get current user from access token or refresh token.
    
    Args:
        authorization_header: Authorization header value (Bearer {token})
        refresh_token: Refresh token from httpOnly cookie
    
    Returns:
        Tuple of (staff_id, role)
    
    Raises:
        HTTPException if authentication fails
    """
    access_token = None
    
    # Extract access token from Authorization header
    if authorization_header:
        parts = authorization_header.split()
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            access_token = parts[1]
        else:
            logger.warning('Invalid Authorization header format')
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid authorization header format'
            )
    
    # Try to verify access token
    if access_token:
        try:
            payload = verify_token(access_token, token_type='access')
            return (payload.sub, payload.role)
        except HTTPException as e:
            if e.status_code == status.HTTP_401_UNAUTHORIZED and 'expired' in str(e.detail).lower():
                # Access token expired, try refresh
                if refresh_token:
                    try:
                        payload = verify_token(refresh_token, token_type='refresh')
                        logger.info('Access token refreshed for staff_id %s', payload.sub)
                        return (payload.sub, payload.role)
                    except HTTPException:
                        logger.warning('Refresh token verification failed')
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail='Authentication required'
                        )
            raise
    
    # No access token, try refresh token
    if refresh_token:
        try:
            payload = verify_token(refresh_token, token_type='refresh')
            logger.info('Authenticated with refresh token for staff_id %s', payload.sub)
            return (payload.sub, payload.role)
        except HTTPException:
            logger.warning('Refresh token verification failed')
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Authentication required'
            )
    
    # No tokens provided
    logger.warning('No authentication tokens provided')
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Authentication required'
    )
