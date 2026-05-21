import asyncio
import signal

from shared.logger import get_logger
from shared.service_heartbeat import get_instance_name, start_heartbeat_task, stop_heartbeat_task
from scheduler import NotificationScheduler

logger = get_logger('notification_service')
SERVICE_TYPE = 'notification_service'
INSTANCE_NAME = get_instance_name('notification_service_main')


async def main() -> None:
    scheduler = NotificationScheduler()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    logger.info('Starting notification_service scheduler')
    await scheduler.start()
    heartbeat_task = start_heartbeat_task(SERVICE_TYPE, INSTANCE_NAME)
    logger.info('notification_service scheduler started')

    await stop_event.wait()

    logger.info('Shutting down notification_service scheduler')
    await stop_heartbeat_task(heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)
    await scheduler.shutdown()
    logger.info('notification_service scheduler stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')
