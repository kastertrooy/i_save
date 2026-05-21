from datetime import datetime, timedelta
import re

from fastapi import APIRouter, Depends, HTTPException, status
import docker
from docker.errors import DockerException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import (
    ContentQueue, User, ServiceInstance, DeliveryLog, DirectMessageLog, MediaCache
)
from shared.logger import get_logger

logger = get_logger('admin_panel')
router = APIRouter(prefix='/api/metrics', tags=['metrics'])
LOG_LINE_RE = re.compile(
    r'^\[(?P<timestamp>[^\]]+)\]\s+\[(?P<service>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<message>.*)$'
)
HIDDEN_SERVICE_TYPES = {'admin_panel', 'browser_service', 'notification_service', 'sender', 'telegram_bot'}
HIDDEN_INSTANCE_NAMES = {
    'admin_panel',
    'browser_service',
    'browser_service_main',
    'notification_service',
    'sender',
    'telegram_bot',
}


def _guess_level(line: str) -> str:
    upper_line = line.upper()
    if 'ERROR' in upper_line or 'EXCEPTION' in upper_line or 'TRACEBACK' in upper_line:
        return 'error'
    if 'WARNING' in upper_line or 'WARN' in upper_line:
        return 'warning'
    if 'DEBUG' in upper_line:
        return 'debug'
    return 'info'


def _parse_log_line(line: str, container_name: str) -> dict:
    parsed = LOG_LINE_RE.match(line)
    if parsed:
        return {
            'container': container_name,
            'service': parsed.group('service'),
            'level': parsed.group('level').lower(),
            'timestamp': parsed.group('timestamp'),
            'message': parsed.group('message'),
        }

    return {
        'container': container_name,
        'service': container_name,
        'level': _guess_level(line),
        'timestamp': None,
        'message': line,
    }


def _is_visible_service(service: ServiceInstance) -> bool:
    return service.service_type not in HIDDEN_SERVICE_TYPES and service.instance_name not in HIDDEN_INSTANCE_NAMES


def _bytes_to_mb(value: int | float | None) -> float:
    return round(float(value or 0) / 1024 / 1024, 1)


def _docker_cpu_percent(stats: dict) -> tuple[float, int]:
    cpu_stats = stats.get('cpu_stats', {}) or {}
    precpu_stats = stats.get('precpu_stats', {}) or {}
    cpu_usage = cpu_stats.get('cpu_usage', {}) or {}
    precpu_usage = precpu_stats.get('cpu_usage', {}) or {}

    cpu_delta = float(cpu_usage.get('total_usage') or 0) - float(precpu_usage.get('total_usage') or 0)
    system_delta = float(cpu_stats.get('system_cpu_usage') or 0) - float(precpu_stats.get('system_cpu_usage') or 0)
    online_cpus = int(cpu_stats.get('online_cpus') or len(cpu_usage.get('percpu_usage') or []) or 1)
    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0, online_cpus
    return round((cpu_delta / system_delta) * online_cpus * 100.0, 1), online_cpus


def _sum_io(stats: dict, section: str, operation: str) -> int:
    entries = stats.get(section, {}) or {}
    if section == 'networks':
        return sum(int(network.get(operation) or 0) for network in entries.values())
    return sum(
        int(entry.get('value') or 0)
        for entry in entries.get('io_service_bytes_recursive', []) or []
        if entry.get('op') == operation
    )


def _is_project_container(container) -> bool:
    attrs = container.attrs or {}
    labels = attrs.get('Config', {}).get('Labels') or {}
    networks = attrs.get('NetworkSettings', {}).get('Networks') or {}
    image_tags = container.image.tags or []
    image_name = image_tags[0] if image_tags else ''
    name = container.name or ''

    return (
        labels.get('com.docker.compose.project') == 'iinstasaveuz' or
        'instatg_network' in networks or
        name.startswith(('iinstasaveuz-', 'sender-', 'downloader-', 'watcher-')) or
        image_name.startswith(('instatg-', 'iinstasaveuz-'))
    )


def _container_service_type(container) -> str:
    labels = (container.attrs or {}).get('Config', {}).get('Labels') or {}
    compose_service = labels.get('com.docker.compose.service')
    if compose_service:
        return compose_service
    name = container.name or ''
    return name.rsplit('-', 1)[0] if '-' in name else name


def _is_project_container_summary(container: dict) -> bool:
    labels = container.get('Labels') or {}
    networks = (container.get('NetworkSettings') or {}).get('Networks') or {}
    names = [name.lstrip('/') for name in container.get('Names') or []]
    image_name = container.get('Image') or ''

    return (
        labels.get('com.docker.compose.project') == 'iinstasaveuz' or
        'instatg_network' in networks or
        any(name.startswith(('iinstasaveuz-', 'sender-', 'downloader-', 'watcher-')) for name in names) or
        image_name.startswith(('instatg-', 'iinstasaveuz-'))
    )


def _container_summary_service_type(container: dict) -> str:
    labels = container.get('Labels') or {}
    compose_service = labels.get('com.docker.compose.service')
    if compose_service:
        return compose_service
    names = [name.lstrip('/') for name in container.get('Names') or []]
    name = names[0] if names else container.get('Id', '')[:12]
    return name.rsplit('-', 1)[0] if '-' in name else name


def _container_summary_item(container: dict) -> dict:
    names = [name.lstrip('/') for name in container.get('Names') or []]
    status = container.get('State') or 'unknown'
    return {
        'id': (container.get('Id') or '')[:12],
        'service_type': _container_summary_service_type(container),
        'instance_name': names[0] if names else (container.get('Id') or '')[:12],
        'status': status,
        'is_alive': status == 'running',
        'started_at': None,
        'uptime_seconds': None,
        'uptime_label': container.get('Status'),
        'cpu_percent': 0.0,
        'cpu_load_percent': 0.0,
        'memory_usage_mb': 0.0,
        'memory_limit_mb': 0.0,
        'memory_percent': 0.0,
        'network_rx_mb': 0.0,
        'network_tx_mb': 0.0,
        'block_read_mb': 0.0,
        'block_write_mb': 0.0,
        'error': None,
    }


def _parse_docker_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    timestamp = value.rstrip('Z')
    if '.' in timestamp:
        head, fraction = timestamp.split('.', 1)
        timestamp = '%s.%s' % (head, fraction[:6])
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


@router.get('/summary')
async def get_metrics_summary(db: AsyncSession = Depends(get_db)) -> dict:
    """Get summary metrics."""
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    tomorrow = today + timedelta(days=1)
    
    # Queue metrics
    pending_stmt = select(func.count()).select_from(ContentQueue).where(ContentQueue.status == 'pending')
    no_telegram_stmt = select(func.count()).select_from(ContentQueue).where(
        ContentQueue.status == 'downloaded'
    )
    failed_stmt = select(func.count()).select_from(ContentQueue).where(ContentQueue.status == 'failed')
    
    pending_result = await db.execute(pending_stmt)
    no_telegram_result = await db.execute(no_telegram_stmt)
    failed_result = await db.execute(failed_stmt)
    
    queue_pending = pending_result.scalar_one() or 0
    queue_no_telegram = no_telegram_result.scalar_one() or 0
    queue_failed = failed_result.scalar_one() or 0
    
    # Successful deliveries created during the current UTC day.
    downloads_today_stmt = select(func.count()).select_from(DeliveryLog).where(
        DeliveryLog.status == 'success',
        DeliveryLog.created_at >= today,
        DeliveryLog.created_at < tomorrow,
    )
    downloads_today_result = await db.execute(downloads_today_stmt)
    downloads_today = downloads_today_result.scalar_one() or 0

    mb_downloaded_today_stmt = select(func.coalesce(func.sum(MediaCache.size_mb), 0.0)).where(
        MediaCache.created_at >= today,
        MediaCache.created_at < tomorrow,
    )
    mb_downloaded_today_result = await db.execute(mb_downloaded_today_stmt)
    mb_downloaded_today = float(mb_downloaded_today_result.scalar_one() or 0.0)
    
    # User metrics
    active_users_stmt = select(func.count(func.distinct(User.id))).select_from(User).where(
        User.subscription_status.in_(['active', 'free_trial'])
    )
    new_users_today_stmt = select(func.count()).select_from(User)
    new_users_week_stmt = select(func.count()).select_from(User)
    
    active_users_result = await db.execute(active_users_stmt)
    new_users_today_result = await db.execute(new_users_today_stmt)
    new_users_week_result = await db.execute(new_users_week_stmt)
    
    active_users = active_users_result.scalar_one() or 0
    new_users_today = new_users_today_result.scalar_one() or 0
    new_users_week = new_users_week_result.scalar_one() or 0
    
    logger.debug('Summary metrics queried')
    
    return {
        'queue_pending': queue_pending,
        'queue_no_telegram': queue_no_telegram,
        'queue_failed': queue_failed,
        'downloaded_today': downloads_today,
        'mb_downloaded_today': mb_downloaded_today,
        'active_users': active_users,
        'new_users_today': new_users_today,
        'new_users_week': new_users_week,
    }


@router.get('/chart')
async def get_metrics_chart(
    period: str = 'auto',
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get metrics for chart visualization."""
    if period not in ['7d', '30d', '90d']:
        period = '7d'
    
    days = {
        '7d': 7,
        '30d': 30,
        '90d': 90,
    }[period]
    
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    start_date = today - timedelta(days=days - 1)
    end_date = today + timedelta(days=1)
    
    # Generate labels (dates)
    labels = []
    current = start_date
    while current < end_date:
        labels.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)

    downloads_stmt = (
        select(
            func.date(DeliveryLog.created_at).label('day'),
            func.count().label('count'),
        )
        .where(
            DeliveryLog.status == 'success',
            DeliveryLog.created_at >= start_date,
            DeliveryLog.created_at < end_date,
        )
        .group_by(func.date(DeliveryLog.created_at))
    )
    downloads_result = await db.execute(downloads_stmt)
    downloads_by_day = {
        str(day): int(count)
        for day, count in downloads_result.all()
    }

    downloads_data = [downloads_by_day.get(label, 0) for label in labels]
    new_users_data = [0] * len(labels)
    
    logger.debug('Chart metrics generated for period %s', period)
    
    return {
        'labels': labels,
        'downloads': downloads_data,
        'new_users': new_users_data,
    }


@router.get('/services/status')
async def get_services_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Get service instances with heartbeat status."""
    now = datetime.utcnow()
    heartbeat_threshold = now - timedelta(minutes=5)
    
    stmt = select(ServiceInstance)
    result = await db.execute(stmt)
    services = result.scalars().all()
    
    services_data = []
    for service in services:
        if not _is_visible_service(service):
            continue
        is_alive = (
            service.status in ('running', 'waiting_for_login', 'starting') and
            service.last_heartbeat_at is not None and
            service.last_heartbeat_at > heartbeat_threshold
        )
        effective_status = service.status if is_alive else 'stopped'
        uptime_seconds = None
        if service.started_at and is_alive:
            uptime_seconds = max(0, int((now - service.started_at).total_seconds()))
        services_data.append({
            'id': service.id,
            'service_type': service.service_type,
            'instance_name': service.instance_name,
            'status': effective_status,
            'is_alive': is_alive,
            'started_at': service.started_at.isoformat() if service.started_at else None,
            'uptime_seconds': uptime_seconds,
            'last_heartbeat_at': service.last_heartbeat_at.isoformat() if service.last_heartbeat_at else None,
        })
    
    logger.debug('Service status queried, %s services', len(services_data))
    
    return {'services': services_data}


@router.get('/resources/status')
async def get_resources_status(include_stats: bool = True) -> dict:
    """Get Docker resource load for project containers."""
    now = datetime.utcnow()

    total_cpu_percent = 0.0
    total_memory_usage = 0
    total_memory_limit = 0
    cpu_capacity_percent = 100.0
    resource_services = []

    try:
        client = docker.from_env()
        if not include_stats:
            resource_services = [
                _container_summary_item(container)
                for container in client.api.containers(all=True)
                if _is_project_container_summary(container)
            ]
            return {
                'available': True,
                'updated_at': now.isoformat(),
                'cpu_percent': 0.0,
                'cpu_capacity_percent': 100.0,
                'cpu_load_percent': 0.0,
                'cpu_available_percent': 100.0,
                'memory_usage_mb': 0.0,
                'memory_limit_mb': 0.0,
                'memory_percent': 0.0,
                'memory_available_percent': 100.0,
                'running_services': sum(1 for service in resource_services if service['is_alive']),
                'total_services': len(resource_services),
                'services': resource_services,
            }

        containers = [
            container
            for container in client.containers.list(all=True)
            if _is_project_container(container)
        ]

        for container in containers:
            is_alive = container.status == 'running'
            started_at = _parse_docker_datetime(
                (container.attrs or {}).get('State', {}).get('StartedAt')
            )
            uptime_seconds = max(0, int((now - started_at).total_seconds())) if is_alive and started_at else None
            item = {
                'id': container.id[:12],
                'service_type': _container_service_type(container),
                'instance_name': container.name,
                'status': container.status,
                'is_alive': is_alive,
                'started_at': started_at.isoformat() if started_at else None,
                'uptime_seconds': uptime_seconds,
                'cpu_percent': 0.0,
                'cpu_load_percent': 0.0,
                'memory_usage_mb': 0.0,
                'memory_limit_mb': 0.0,
                'memory_percent': 0.0,
                'network_rx_mb': 0.0,
                'network_tx_mb': 0.0,
                'block_read_mb': 0.0,
                'block_write_mb': 0.0,
                'error': None,
            }

            if not is_alive or not include_stats:
                resource_services.append(item)
                continue

            stats = container.stats(stream=False)
            cpu_percent, online_cpus = _docker_cpu_percent(stats)
            cpu_capacity_percent = max(cpu_capacity_percent, online_cpus * 100.0)
            memory_stats = stats.get('memory_stats', {}) or {}
            memory_usage = int(memory_stats.get('usage') or 0)
            memory_limit = int(memory_stats.get('limit') or 0)
            memory_percent = round((memory_usage / memory_limit) * 100.0, 1) if memory_limit else 0.0

            item.update({
                'cpu_percent': cpu_percent,
                'cpu_load_percent': round(min(100.0, cpu_percent / max(online_cpus, 1)), 1),
                'memory_usage_mb': _bytes_to_mb(memory_usage),
                'memory_limit_mb': _bytes_to_mb(memory_limit),
                'memory_percent': memory_percent,
                'network_rx_mb': _bytes_to_mb(_sum_io(stats, 'networks', 'rx_bytes')),
                'network_tx_mb': _bytes_to_mb(_sum_io(stats, 'networks', 'tx_bytes')),
                'block_read_mb': _bytes_to_mb(_sum_io(stats, 'blkio_stats', 'Read')),
                'block_write_mb': _bytes_to_mb(_sum_io(stats, 'blkio_stats', 'Write')),
            })
            total_cpu_percent += cpu_percent
            total_memory_usage += memory_usage
            total_memory_limit = max(total_memory_limit, memory_limit)
            resource_services.append(item)
    except DockerException as exc:
        logger.warning('Failed to read Docker resource stats: %s', exc)
        return {
            'available': False,
            'error': str(exc),
            'updated_at': now.isoformat(),
            'services': [],
        }

    cpu_load_percent = round(min(100.0, (total_cpu_percent / cpu_capacity_percent) * 100.0), 1)
    memory_percent = round((total_memory_usage / total_memory_limit) * 100.0, 1) if total_memory_limit else 0.0

    return {
        'available': True,
        'updated_at': now.isoformat(),
        'cpu_percent': round(total_cpu_percent, 1),
        'cpu_capacity_percent': round(cpu_capacity_percent, 1),
        'cpu_load_percent': cpu_load_percent,
        'cpu_available_percent': round(max(0.0, 100.0 - cpu_load_percent), 1),
        'memory_usage_mb': _bytes_to_mb(total_memory_usage),
        'memory_limit_mb': _bytes_to_mb(total_memory_limit),
        'memory_percent': memory_percent,
        'memory_available_percent': round(max(0.0, 100.0 - memory_percent), 1),
        'running_services': sum(1 for service in resource_services if service['is_alive']),
        'total_services': len(resource_services),
        'services': resource_services,
    }


@router.get('/direct/recent')
async def get_recent_direct_messages(
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get recent Instagram Direct messages observed by watcher."""
    safe_limit = min(max(limit, 1), 5)
    stmt = (
        select(DirectMessageLog)
        .order_by(DirectMessageLog.created_at.desc(), DirectMessageLog.id.desc())
        .limit(max(safe_limit * 4, 20))
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    instagram_ids = {message.instagram_id for message in messages if message.instagram_id}
    usernames_by_instagram_id = {}
    if instagram_ids:
        users_result = await db.execute(
            select(User.instagram_id, User.instagram_username).where(User.instagram_id.in_(instagram_ids))
        )
        usernames_by_instagram_id = {
            instagram_id: instagram_username
            for instagram_id, instagram_username in users_result.all()
            if instagram_username
        }

    def get_username(message: DirectMessageLog) -> str | None:
        raw_data = message.raw_data if isinstance(message.raw_data, dict) else {}
        username = raw_data.get('username')
        if username:
            return username
        user_data = raw_data.get('user')
        if isinstance(user_data, dict):
            username = user_data.get('username')
            if username:
                return username
        return usernames_by_instagram_id.get(message.instagram_id)

    unique_messages = []
    seen_message_ids = set()
    for message in messages:
        if message.message_id in seen_message_ids:
            continue
        seen_message_ids.add(message.message_id)
        unique_messages.append(message)
        if len(unique_messages) >= safe_limit:
            break

    return {
        'messages': [
            {
                'id': message.id,
                'message_id': message.message_id,
                'instagram_id': message.instagram_id,
                'username': get_username(message),
                'text': message.text,
                'url': message.url,
                'content_type': message.content_type,
                'status': message.status,
                'error': message.error,
                'instagram_timestamp': (
                    message.raw_data.get('timestamp')
                    if isinstance(message.raw_data, dict)
                    else None
                ),
                'created_at': message.created_at.isoformat() if message.created_at else None,
            }
            for message in unique_messages
        ]
    }


@router.get('/queue/recent')
async def get_recent_queue_items(
    limit: int = 8,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get recent queue items with all statuses."""
    safe_limit = min(max(limit, 1), 20)
    stmt = (
        select(ContentQueue, User)
        .outerjoin(User, User.instagram_id == ContentQueue.instagram_id)
        .order_by(ContentQueue.id.desc())
        .limit(safe_limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return {
        'items': [
            {
                'id': queue_item.id,
                'instagram_id': queue_item.instagram_id,
                'instagram_username': user.instagram_username if user else None,
                'telegram_chat_id': user.telegram_chat_id if user else None,
                'telegram_username': user.telegram_username if user else None,
                'url': queue_item.url,
                'content_type': queue_item.content_type,
                'status': queue_item.status,
                'retry_count': queue_item.retry_count or 0,
            }
            for queue_item, user in rows
        ]
    }


@router.get('/queue/{queue_id}/logs')
async def get_queue_item_logs(
    queue_id: int,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get database and container logs related to a queue item."""
    queue_item = await db.get(ContentQueue, queue_id)
    if not queue_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Queue item not found',
        )

    user_result = await db.execute(
        select(User).where(User.instagram_id == queue_item.instagram_id)
    )
    user = user_result.scalar_one_or_none()

    delivery_result = await db.execute(
        select(DeliveryLog)
        .where(DeliveryLog.content_queue_id == queue_id)
        .order_by(DeliveryLog.id.desc())
    )
    delivery_logs = delivery_result.scalars().all()

    direct_result = await db.execute(
        select(DirectMessageLog)
        .where(
            DirectMessageLog.instagram_id == queue_item.instagram_id,
            or_(
                DirectMessageLog.url == queue_item.url,
                DirectMessageLog.url.is_(None),
            ),
        )
        .order_by(DirectMessageLog.id.desc())
        .limit(50)
    )
    direct_logs = direct_result.scalars().all()

    terms = {
        f'content_queue id={queue_id}',
        f'queue id {queue_id}',
        f'queue_id={queue_id}',
        queue_item.instagram_id,
        queue_item.url,
    }
    terms = {term.lower() for term in terms if term}
    start_date = datetime.utcnow() - timedelta(days=max(1, min(days, 30)))
    container_logs = []

    try:
        client = docker.from_env()
        for container in client.containers.list(all=True):
            container_name = container.name
            if not any(name in container_name.lower() for name in ('downloader', 'sender', 'watcher')):
                continue

            raw_logs = container.logs(since=start_date, tail=2000).decode('utf-8', errors='replace')
            for raw_line in raw_logs.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                lower_line = line.lower()
                if not any(term in lower_line for term in terms):
                    continue

                parsed = _parse_log_line(line, container_name)
                parsed['source'] = 'container'
                container_logs.append(parsed)
    except DockerException as exc:
        logger.warning('Failed to read related queue docker logs for item %s: %s', queue_id, exc)
        container_logs.append({
            'source': 'container',
            'container': None,
            'service': 'docker',
            'level': 'warning',
            'timestamp': None,
            'message': 'Failed to read Docker logs: %s' % exc,
        })

    return {
        'item': {
            'id': queue_item.id,
            'instagram_id': queue_item.instagram_id,
            'instagram_username': user.instagram_username if user else None,
            'telegram_chat_id': user.telegram_chat_id if user else None,
            'telegram_username': user.telegram_username if user else None,
            'url': queue_item.url,
            'content_type': queue_item.content_type,
            'status': queue_item.status,
            'retry_count': queue_item.retry_count or 0,
        },
        'delivery_logs': [
            {
                'id': log.id,
                'source': 'delivery',
                'level': 'error' if log.status == 'failed' else 'info',
                'timestamp': log.created_at.isoformat() if log.created_at else None,
                'message': '%s delivery status=%s user_id=%s'
                % (log.delivery_type, log.status, log.user_id),
            }
            for log in delivery_logs
        ],
        'direct_logs': [
            {
                'id': log.id,
                'source': 'direct',
                'level': 'error' if log.error else 'info',
                'timestamp': log.created_at.isoformat() if log.created_at else None,
                'message': 'Direct message status=%s content_type=%s error=%s'
                % (log.status, log.content_type or 'unknown', log.error or 'none'),
            }
            for log in direct_logs
        ],
        'container_logs': container_logs[-200:],
    }


@router.get('/queue/pending/recent')
async def get_recent_pending_queue(
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Backward-compatible alias for recent queue items."""
    return await get_recent_queue_items(limit=limit, db=db)
