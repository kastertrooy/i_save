from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import BruteForceLog
from shared.logger import get_logger

logger = get_logger('admin_panel')


async def check_brute_force(ip: str, db: AsyncSession) -> int | None:
    """
    Check if IP is blocked by brute force protection.
    
    Args:
        ip: Client IP address
        db: Database session
    
    Returns:
        None if not blocked, or remaining seconds until unblock
    """
    stmt = select(BruteForceLog).where(BruteForceLog.ip_address == ip)
    result = await db.execute(stmt)
    log = result.scalar_one_or_none()
    
    if not log or not log.blocked_until:
        return None
    
    now = datetime.utcnow()
    if log.blocked_until <= now:
        # Block period expired
        log.failed_attempts = 0
        log.blocked_until = None
        log.block_duration_sec = None
        await db.commit()
        return None
    
    remaining_seconds = int((log.blocked_until - now).total_seconds())
    return remaining_seconds


async def register_failed_attempt(ip: str, username: str, db: AsyncSession) -> int:
    """
    Register a failed login attempt for an IP address.
    
    Args:
        ip: Client IP address
        username: Staff username attempted
        db: Database session
    
    Returns:
        Seconds of blocking if triggered, 0 otherwise
    """
    stmt = select(BruteForceLog).where(BruteForceLog.ip_address == ip)
    result = await db.execute(stmt)
    log = result.scalar_one_or_none()
    
    if not log:
        log = BruteForceLog(ip_address=ip, failed_attempts=1)
        db.add(log)
        await db.commit()
        logger.info('First failed attempt recorded for IP %s, username %s', ip, username)
        return 0
    
    # Check if currently blocked
    if log.blocked_until and log.blocked_until > datetime.utcnow():
        remaining = int((log.blocked_until - datetime.utcnow()).total_seconds())
        logger.warning('IP %s attempted login while blocked, %s seconds remaining', ip, remaining)
        return remaining
    
    log.failed_attempts += 1
    attempt_num = log.failed_attempts
    
    if attempt_num >= 3:
        # Calculate block duration: 60 * 2^(attempt_num - 3)
        # Attempt 3: 60 * 2^0 = 60 seconds
        # Attempt 4: 60 * 2^1 = 120 seconds
        # Attempt 5: 60 * 2^2 = 240 seconds, etc.
        block_duration_sec = 60 * (2 ** (attempt_num - 3))
        log.blocked_until = datetime.utcnow() + timedelta(seconds=block_duration_sec)
        log.block_duration_sec = block_duration_sec
        
        await db.commit()
        logger.warning(
            'IP %s blocked for %s seconds after %s failed attempts',
            ip, block_duration_sec, attempt_num
        )
        return block_duration_sec
    else:
        await db.commit()
        logger.info('Failed attempt %s recorded for IP %s, username %s', attempt_num, ip, username)
        return 0


async def reset_attempts(ip: str, db: AsyncSession) -> None:
    """
    Reset failed login attempts for an IP (called after successful login).
    
    Args:
        ip: Client IP address
        db: Database session
    """
    stmt = select(BruteForceLog).where(BruteForceLog.ip_address == ip)
    result = await db.execute(stmt)
    log = result.scalar_one_or_none()
    
    if log:
        log.failed_attempts = 0
        log.blocked_until = None
        log.block_duration_sec = None
        await db.commit()
        logger.info('Brute force attempts reset for IP %s after successful login', ip)
