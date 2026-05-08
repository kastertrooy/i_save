from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query, Header, Cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import ServiceInstance
from shared.logger import get_logger
from admin_panel.middleware.auth_middleware import get_current_user
from admin_panel.services.docker_manager import DockerManager

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/services', tags=['services'])


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
async def list_services(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """List all service instances."""
    stmt = select(ServiceInstance)
    result = await db.execute(stmt)
    services = result.scalars().all()
    
    return [
        {
            'id': s.id,
            'service_type': s.service_type,
            'instance_name': s.instance_name,
            'status': s.status,
            'container_id': s.container_id,
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
    
    try:
        docker_manager = DockerManager()
        container_id = docker_manager.start_watcher(account_id, instance_name)
        
        # Register service instance in DB
        service = ServiceInstance(
            service_type='watcher',
            instance_name=instance_name,
            status='running',
            container_id=container_id,
            last_heartbeat_at=datetime.utcnow(),
        )
        db.add(service)
        await db.commit()
        
        logger.info(
            'Admin %s started watcher service %s for account %s (container %s)',
            staff_id, instance_name, account_id, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
    except Exception as e:
        logger.error('Failed to start watcher: %s', str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to start service'
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
    
    try:
        docker_manager = DockerManager()
        container_id = docker_manager.start_downloader(position, storage_group_id, instance_name)
        
        # Register service instance in DB
        service = ServiceInstance(
            service_type='downloader',
            instance_name=instance_name,
            status='running',
            container_id=container_id,
            queue_start_position=position,
            last_heartbeat_at=datetime.utcnow(),
        )
        db.add(service)
        await db.commit()
        
        logger.info(
            'Admin %s started downloader service %s at position %s (container %s)',
            staff_id, instance_name, position, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
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
    
    try:
        docker_manager = DockerManager()
        container_id = docker_manager.start_sender(instance_name)
        
        # Register service instance in DB
        service = ServiceInstance(
            service_type='sender',
            instance_name=instance_name,
            status='running',
            container_id=container_id,
            last_heartbeat_at=datetime.utcnow(),
        )
        db.add(service)
        await db.commit()
        
        logger.info(
            'Admin %s started sender service %s (container %s)',
            staff_id, instance_name, container_id
        )
        
        return {
            'container_id': container_id,
            'instance_name': instance_name,
            'status': 'running'
        }
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
            docker_manager.stop_container(service.container_id)
        
        service.status = 'stopped'
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
        if service.container_id:
            docker_manager.restart_container(service.container_id)
        
        service.last_heartbeat_at = datetime.utcnow()
        await db.commit()
        
        logger.info('Admin %s restarted service %s', staff_id, instance_id)
        
        return {'status': 'running', 'instance_id': instance_id}
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
            container = docker_manager.client.containers.get(service.container_id)
            logs_bytes = container.logs(tail=lines)
            logs_output = logs_bytes.decode('utf-8', errors='replace').split('\n')
        
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
