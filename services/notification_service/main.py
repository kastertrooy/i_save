import asyncio
import signal

from shared.logger import get_logger
from scheduler import NotificationScheduler

logger = get_logger('notification_service')


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
    logger.info('notification_service scheduler started')

    await stop_event.wait()

    logger.info('Shutting down notification_service scheduler')
    await scheduler.shutdown()
    logger.info('notification_service scheduler stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')
