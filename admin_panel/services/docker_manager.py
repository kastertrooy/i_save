import os
from typing import Optional

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from shared.logger import get_logger

logger = get_logger('admin_panel')

DOCKER_NETWORK = os.getenv('DOCKER_NETWORK', 'instatg_network')


def _shared_environment() -> dict:
    return {
        'DATABASE_URL': os.getenv('DATABASE_URL', ''),
        'REDIS_URL': os.getenv('REDIS_URL', ''),
        'ENCRYPTION_KEY': os.getenv('ENCRYPTION_KEY', ''),
        'JWT_SECRET_KEY': os.getenv('JWT_SECRET_KEY', ''),
        'JWT_ALGORITHM': os.getenv('JWT_ALGORITHM', 'HS256'),
        'JWT_ACCESS_TOKEN_EXPIRE_MINUTES': os.getenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '15'),
        'JWT_REFRESH_TOKEN_EXPIRE_DAYS': os.getenv('JWT_REFRESH_TOKEN_EXPIRE_DAYS', '7'),
        'TELEGRAM_BOT_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'TELEGRAM_BOT_USERNAME': os.getenv('TELEGRAM_BOT_USERNAME', ''),
        'ADMIN_TELEGRAM_CHAT_ID': os.getenv('ADMIN_TELEGRAM_CHAT_ID', '0'),
        'DOCKER_SOCKET': os.getenv('DOCKER_SOCKET', '/var/run/docker.sock'),
        'TEMP_DOWNLOAD_PATH': os.getenv('TEMP_DOWNLOAD_PATH', '/app/temp/downloads'),
        'MAX_VIDEO_SIZE_MB': os.getenv('MAX_VIDEO_SIZE_MB', '1024'),
    }


class DockerManager:
    """Manager for Docker container operations."""
    
    def __init__(self) -> None:
        """Initialize Docker client."""
        try:
            self.client = docker.from_env()
            logger.info('Docker client initialized')
        except Exception as e:
            logger.error('Failed to initialize Docker client: %s', str(e))
            raise

    def start_watcher(self, account_id: int, instance_name: str) -> str:
        """
        Start Instagram watcher container.
        
        Args:
            account_id: Instagram account ID
            instance_name: Name for the container instance
        
        Returns:
            Container ID
        """
        try:
            environment = {
                **_shared_environment(),
                'INSTAGRAM_ACCOUNT_ID': str(account_id),
                'SERVICE_INSTANCE_NAME': instance_name,
                'HEADLESS': 'True',
                'BROWSER_DEBUG_DIR': os.getenv('BROWSER_DEBUG_DIR', '/app/temp/browser_debug'),
                'MANUAL_BROWSER': '0',
                'NOVNC_PORT': os.getenv('NOVNC_PORT', '6080'),
                'COOKIES_DIR': os.getenv('COOKIES_DIR', '/app/Cookies'),
                'INITIAL_INBOX_PROCESS_WINDOW_MINUTES': os.getenv('INITIAL_INBOX_PROCESS_WINDOW_MINUTES', '120'),
            }
            
            container = self.client.containers.run(
                image='instatg-watcher:latest',
                name=instance_name,
                environment=environment,
                detach=True,
                restart_policy={'Name': 'on-failure', 'MaximumRetryCount': 1},
                network=DOCKER_NETWORK,
            )
            
            logger.info('Started watcher container %s for account %s', container.id, account_id)
            return container.id
        except Exception as e:
            logger.error('Failed to start watcher container: %s', str(e))
            raise

    def start_downloader(self, position: int, storage_group_id: int, instance_name: str) -> str:
        """
        Start media downloader container.
        
        Args:
            position: Queue start position for this downloader
            storage_group_id: Telegram storage group ID
            instance_name: Name for the container instance
        
        Returns:
            Container ID
        """
        try:
            environment = {
                **_shared_environment(),
                'QUEUE_START_POSITION': str(position),
                'STORAGE_GROUP_ID': str(storage_group_id),
                'SERVICE_INSTANCE_NAME': instance_name,
            }
            
            container = self.client.containers.run(
                image='instatg-downloader:latest',
                name=instance_name,
                environment=environment,
                detach=True,
                volumes={
                    'iinstasaveuz_temp_downloads': {
                        'bind': os.getenv('TEMP_DOWNLOAD_PATH', '/app/temp/downloads'),
                        'mode': 'rw',
                    }
                },
                restart_policy={'Name': 'unless-stopped'},
                network=DOCKER_NETWORK,
            )
            
            logger.info(
                'Started downloader container %s at position %s for storage group %s',
                container.id, position, storage_group_id
            )
            return container.id
        except Exception as e:
            logger.error('Failed to start downloader container: %s', str(e))
            raise

    def start_sender(self, instance_name: str) -> str:
        """
        Start media sender container.
        
        Args:
            instance_name: Name for the container instance
        
        Returns:
            Container ID
        """
        try:
            container = self.client.containers.run(
                image='instatg-sender:latest',
                name=instance_name,
                environment={**_shared_environment(), 'SERVICE_INSTANCE_NAME': instance_name},
                detach=True,
                restart_policy={'Name': 'unless-stopped'},
                network=DOCKER_NETWORK,
            )
            
            logger.info('Started sender container %s', container.id)
            return container.id
        except Exception as e:
            logger.error('Failed to start sender container: %s', str(e))
            raise

    def stop_container(self, container_id: str) -> None:
        """
        Stop a container.
        
        Args:
            container_id: Docker container ID
        """
        try:
            container = self.client.containers.get(container_id)
            container.stop()
            logger.info('Stopped container %s', container_id)
        except NotFound:
            logger.warning('Container %s was already removed', container_id)
        except Exception as e:
            logger.error('Failed to stop container %s: %s', container_id, str(e))
            raise

    def remove_container(self, container_id: str, force: bool = True) -> None:
        """Remove a container, ignoring already removed containers."""
        try:
            container = self.client.containers.get(container_id)
            container.remove(force=force)
            logger.info('Removed container %s', container_id)
        except NotFound:
            logger.warning('Container %s was already removed', container_id)
        except Exception as e:
            logger.error('Failed to remove container %s: %s', container_id, str(e))
            raise

    def get_container_environment(self, container_id: str) -> dict:
        """Return container environment variables as a dictionary."""
        try:
            container = self.client.containers.get(container_id)
            env_values = container.attrs.get('Config', {}).get('Env', []) or []
            result = {}
            for item in env_values:
                key, _, value = item.partition('=')
                result[key] = value
            return result
        except NotFound:
            logger.warning('Container %s was already removed', container_id)
            return {}
        except Exception as e:
            logger.error('Failed to inspect container %s environment: %s', container_id, str(e))
            raise

    def restart_container(self, container_id: str) -> None:
        """
        Restart a container.
        
        Args:
            container_id: Docker container ID
        """
        try:
            container = self.client.containers.get(container_id)
            container.restart()
            logger.info('Restarted container %s', container_id)
        except NotFound:
            logger.warning('Container %s was already removed', container_id)
            raise
        except Exception as e:
            logger.error('Failed to restart container %s: %s', container_id, str(e))
            raise

    def get_container_status(self, container_id: str) -> str:
        """
        Get container status.
        
        Args:
            container_id: Docker container ID
        
        Returns:
            Container status (e.g., 'running', 'exited', 'paused')
        """
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except Exception as e:
            logger.error('Failed to get container status %s: %s', container_id, str(e))
            raise

    def list_running_containers(self) -> list[dict]:
        """
        List all running containers.
        
        Returns:
            List of dicts with container info (id, name, status, image)
        """
        try:
            containers = self.client.containers.list(filters={'status': 'running'})
            result = [
                {
                    'id': container.id,
                    'name': container.name,
                    'status': container.status,
                    'image': container.image.tags[0] if container.image.tags else 'unknown',
                }
                for container in containers
            ]
            logger.debug('Listed %s running containers', len(result))
            return result
        except Exception as e:
            logger.error('Failed to list running containers: %s', str(e))
            raise
