from datetime import datetime, timedelta
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
import httpx
from docker.errors import APIError, NotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.database.connection import get_db
from shared.database.models import ServiceInstance
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user
from admin_panel.services.docker_manager import DockerManager

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/services', tags=['services'])
BROWSER_SERVICE_TIMEOUT = 60.0
INSTANCE_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{1,62}$')
ACTIVE_SERVICE_STATUSES = {'running', 'waiting_for_login', 'starting'}


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


def _validate_instance_name(instance_name: str) -> None:
    if not INSTANCE_NAME_RE.fullmatch(instance_name or ''):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                'Instance name must be 2-63 characters, start with a Latin letter, '
                'and contain only Latin letters, numbers, dots, underscores, or hyphens. '
                'Example: watcher-1'
            ),
        )


def _is_alive(service: ServiceInstance, now: datetime) -> bool:
    heartbeat_threshold = now - timedelta(minutes=5)
    return (
        service.status in ACTIVE_SERVICE_STATUSES and
        service.last_heartbeat_at is not None and
        service.last_heartbeat_at > heartbeat_threshold
    )


async def _active_services(db: AsyncSession, *service_types: str) -> list[ServiceInstance]:
    now = datetime.utcnow()
    stmt = select(ServiceInstance)
    if service_types:
        stmt = stmt.where(ServiceInstance.service_type.in_(service_types))
    result = await db.execute(stmt)
    return [service for service in result.scalars().all() if _is_alive(service, now)]


async def _ensure_instance_name_available(
    db: AsyncSession,
    instance_name: str,
) -> None:
    for service in await _active_services(db):
        if service.instance_name == instance_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Active service with instance name "{instance_name}" already exists'
            )


async def _ensure_no_duplicate_watcher(
    db: AsyncSession,
    docker_manager: DockerManager,
    account_id: int,
) -> None:
    for service in await _active_services(db, 'watcher', 'instagram_watcher'):
        if not service.container_id:
            continue
        container_env = docker_manager.get_container_environment(service.container_id)
        if container_env.get('INSTAGRAM_ACCOUNT_ID') == str(account_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Watcher for Instagram account {account_id} is already running as {service.instance_name}'
            )


async def _ensure_no_duplicate_downloader(
    db: AsyncSession,
    docker_manager: DockerManager,
    position: int,
    storage_group_id: int,
) -> None:
    for service in await _active_services(db, 'downloader'):
        if service.queue_start_position != position:
            continue
        if not service.container_id:
            continue
        container_env = docker_manager.get_container_environment(service.container_id)
        if container_env.get('STORAGE_GROUP_ID') == str(storage_group_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f'Downloader for queue position {position} and storage group '
                    f'{storage_group_id} is already running as {service.instance_name}'
                )
            )


async def _ensure_no_duplicate_sender(db: AsyncSession) -> None:
    active_senders = await _active_services(db, 'sender')
    if active_senders:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Sender is already running as {active_senders[0].instance_name}'
        )


async def _record_started_service(
    db: AsyncSession,
    service_type: str,
    instance_name: str,
    container_id: str,
    started_at: datetime,
    queue_start_position: Optional[int] = None,
) -> None:
    stmt = select(ServiceInstance).where(
        ServiceInstance.service_type == service_type,
        ServiceInstance.instance_name == instance_name,
    )
    result = await db.execute(stmt)
    service = result.scalar_one_or_none()
    if service:
        service.status = 'running'
        service.container_id = container_id
        service.started_at = started_at
        service.last_heartbeat_at = started_at
        if queue_start_position is not None:
            service.queue_start_position = queue_start_position
    else:
        db.add(ServiceInstance(
            service_type=service_type,
            instance_name=instance_name,
            status='running',
            container_id=container_id,
            queue_start_position=queue_start_position,
            started_at=started_at,
            last_heartbeat_at=started_at,
        ))
    await db.commit()


async def _upsert_service_instance(
    db: AsyncSession,
    service_type: str,
    instance_name: str,
    status_value: str,
    container_id: Optional[str] = None,
) -> None:
    stmt = select(ServiceInstance).where(
        ServiceInstance.service_type == service_type,
        ServiceInstance.instance_name == instance_name,
    )
    result = await db.execute(stmt)
    service = result.scalar_one_or_none()
    now = datetime.utcnow()
    if service:
        service.status = status_value
        service.last_heartbeat_at = now
        if status_value in ACTIVE_SERVICE_STATUSES:
            service.started_at = service.started_at or now
        if container_id is not None:
            service.container_id = container_id
    else:
        db.add(ServiceInstance(
            service_type=service_type,
            instance_name=instance_name,
            status=status_value,
            container_id=container_id,
            started_at=now if status_value in ACTIVE_SERVICE_STATUSES else None,
            last_heartbeat_at=now,
        ))
    await db.commit()


async def _call_browser_service(method: str, path: str, **kwargs) -> dict:
    url = f'{settings.browser_service_url.rstrip("/")}{path}'
    async with httpx.AsyncClient(timeout=BROWSER_SERVICE_TIMEOUT) as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()


@router.get('/')
async def list_services(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """List all service instances."""
    stmt = select(ServiceInstance)
    result = await db.execute(stmt)
    services = result.scalars().all()
    heartbeat_threshold = datetime.utcnow() - timedelta(minutes=5)
    
    return [
        {
            'id': s.id,
            'service_type': s.service_type,
            'instance_name': s.instance_name,
            'status': s.status if s.last_heartbeat_at and s.last_heartbeat_at > heartbeat_threshold else 'stopped',
            'container_id': s.container_id,
            'started_at': s.started_at.isoformat() if s.started_at else None,
            'uptime_seconds': int((datetime.utcnow() - s.started_at).total_seconds()) if s.started_at else None,
            'last_heartbeat_at': s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None,
        }
        for s in services
    ]


@router.post('/watcher/start')
async def start_watcher(
    account_id: int = Query(...),
    instance_name: str = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Start Instagram watcher service."""
    staff_id, role = _require_admin(authorization, refresh_token)
    _validate_instance_name(instance_name)
    
    try:
        docker_manager = DockerManager()
        await _ensure_instance_name_available(db, instance_name)
        await _ensure_no_duplicate_watcher(db, docker_manager, account_id)
        now = datetime.utcnow()
        container_id = docker_manager.start_watcher(account_id, instance_name)
        
        await _record_started_service(db, 'instagram_watcher', instance_name, container_id, now)
        
        logger.info(
            'Admin %s started watcher service %s for account %s (container %s)',
            staff_id, instance_name, account_id, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
    except APIError as e:
        logger.error('Docker failed to start watcher: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Docker failed to start watcher: {str(e)}'
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to start watcher: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to start service'
        )


@router.post('/watcher/open-browser')
async def open_watcher_browser(
    account_id: int = Query(...),
    instance_name: str = Query('instagram_watcher_main'),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Open visible Instagram browser for manual login."""
    staff_id, role = _require_admin(authorization, refresh_token)
    _validate_instance_name(instance_name)

    try:
        await _call_browser_service(
            'POST',
            '/session/start-manual-login',
            json={'account_id': account_id},
        )
        await _upsert_service_instance(db, 'watcher', instance_name, 'waiting_for_login')
        logger.info(
            'Admin %s opened manual login browser for watcher %s account %s',
            staff_id, instance_name, account_id
        )
        return {
            'status': 'waiting_for_login',
            'instance_name': instance_name,
            'novnc_url': settings.browser_novnc_url,
        }
    except httpx.HTTPError as exc:
        logger.error('Failed to open browser_service manual login: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Browser service is unavailable'
        )


@router.post('/watcher/confirm-login')
async def confirm_watcher_login(
    instance_name: str = Query('instagram_watcher_main'),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Confirm manual Instagram login and save cookies."""
    staff_id, role = _require_admin(authorization, refresh_token)

    try:
        data = await _call_browser_service('POST', '/session/confirm-login')
        if data.get('success'):
            await _upsert_service_instance(db, 'watcher', instance_name, 'running')
            logger.info('Admin %s confirmed manual login for watcher %s', staff_id, instance_name)
        return data
    except httpx.HTTPError as exc:
        logger.error('Failed to confirm browser_service manual login: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Browser service is unavailable'
        )


@router.get('/watcher/login-status')
async def watcher_login_status(
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None)
) -> dict:
    """Return current manual Instagram login status."""
    _require_admin(authorization, refresh_token)

    try:
        data = await _call_browser_service('GET', '/session/login-status')
        data['novnc_url'] = settings.browser_novnc_url
        return data
    except httpx.HTTPError as exc:
        logger.error('Failed to poll browser_service manual login status: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Browser service is unavailable'
        )


@router.post('/downloader/start')
async def start_downloader(
    position: int = Query(...),
    storage_group_id: int = Query(...),
    instance_name: str = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Start media downloader service."""
    staff_id, role = _require_admin(authorization, refresh_token)
    _validate_instance_name(instance_name)
    
    try:
        docker_manager = DockerManager()
        await _ensure_instance_name_available(db, instance_name)
        await _ensure_no_duplicate_downloader(db, docker_manager, position, storage_group_id)
        now = datetime.utcnow()
        container_id = docker_manager.start_downloader(position, storage_group_id, instance_name)
        
        await _record_started_service(
            db,
            'downloader',
            instance_name,
            container_id,
            now,
            queue_start_position=position,
        )
        
        logger.info(
            'Admin %s started downloader service %s at position %s (container %s)',
            staff_id, instance_name, position, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to start downloader: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to start service'
        )


@router.post('/sender/start')
async def start_sender(
    instance_name: str = Query(...),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Start media sender service."""
    staff_id, role = _require_admin(authorization, refresh_token)
    _validate_instance_name(instance_name)
    
    try:
        docker_manager = DockerManager()
        await _ensure_instance_name_available(db, instance_name)
        await _ensure_no_duplicate_sender(db)
        now = datetime.utcnow()
        container_id = docker_manager.start_sender(instance_name)
        
        await _record_started_service(db, 'sender', instance_name, container_id, now)
        
        logger.info(
            'Admin %s started sender service %s (container %s)',
            staff_id, instance_name, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to start sender: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to start service'
        )


@router.post('/{instance_id}/stop')
async def stop_service(
    instance_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Stop a service instance."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(ServiceInstance).where(ServiceInstance.id == instance_id)
    result = await db.execute(stmt)
    service = result.scalar_one_or_none()
    
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Service not found'
        )
    
    try:
        docker_manager = DockerManager()
        if service.container_id:
            docker_manager.remove_container(service.container_id, force=True)

        await db.delete(service)
        await db.commit()
        
        logger.info('Admin %s stopped service %s', staff_id, instance_id)
        
        return {'status': 'stopped', 'instance_id': instance_id}
    except Exception as e:
        logger.error('Failed to stop service: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to stop service'
        )


@router.post('/{instance_id}/restart')
async def restart_service(
    instance_id: int,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Restart a service instance."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(ServiceInstance).where(ServiceInstance.id == instance_id)
    result = await db.execute(stmt)
    service = result.scalar_one_or_none()
    
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Service not found'
        )
    
    try:
        docker_manager = DockerManager()
        container_env = {}
        if service.container_id:
            container_env = docker_manager.get_container_environment(service.container_id)
            docker_manager.remove_container(service.container_id, force=True)

        container_id = None
        if service.service_type in ('watcher', 'instagram_watcher'):
            account_id_value = container_env.get('INSTAGRAM_ACCOUNT_ID')
            if not account_id_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Watcher restart requires an existing container with INSTAGRAM_ACCOUNT_ID'
                )
            container_id = docker_manager.start_watcher(int(account_id_value), service.instance_name)
        if service.service_type == 'downloader':
            if service.queue_start_position is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Downloader restart requires queue start position'
                )
            storage_group_id_value = container_env.get('STORAGE_GROUP_ID')
            if not storage_group_id_value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Downloader restart requires an existing container with STORAGE_GROUP_ID'
                )
            container_id = docker_manager.start_downloader(
                service.queue_start_position,
                int(storage_group_id_value),
                service.instance_name,
            )
        if service.service_type == 'sender':
            container_id = docker_manager.start_sender(service.instance_name)
        if service.service_type not in ('watcher', 'instagram_watcher', 'downloader', 'sender'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Restart is not supported for service type {service.service_type}'
            )

        service.container_id = container_id
        service.status = 'running'
        service.started_at = datetime.utcnow()
        service.last_heartbeat_at = service.started_at
        await db.commit()
        
        logger.info('Admin %s restarted service %s', staff_id, instance_id)
        
        return {'status': 'running', 'instance_id': instance_id, 'container_id': container_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to restart service: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to restart service'
        )


@router.get('/{instance_id}/logs')
async def get_service_logs(
    instance_id: int,
    lines: int = Query(100, ge=1, le=1000),
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get last N lines of container logs."""
    staff_id, role = _require_admin(authorization, refresh_token)
    
    stmt = select(ServiceInstance).where(ServiceInstance.id == instance_id)
    result = await db.execute(stmt)
    service = result.scalar_one_or_none()
    
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Service not found'
        )
    
    try:
        logs_output = []
        if service.container_id:
            docker_manager = DockerManager()
            try:
                container = docker_manager.client.containers.get(service.container_id)
                logs_bytes = container.logs(tail=lines)
                logs_output = logs_bytes.decode('utf-8', errors='replace').split('\n')
            except NotFound:
                logs_output = [f'Container {service.container_id} is not available']
        
        logger.info('Admin %s requested logs for service %s', staff_id, instance_id)
        
        return {
            'instance_id': instance_id,
            'instance_name': service.instance_name,
            'logs': logs_output
        }
    except Exception as e:
        logger.error('Failed to get service logs: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to retrieve logs'
        )
