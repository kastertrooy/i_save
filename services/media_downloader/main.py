import asyncio
import os
import signal

from shared.logger import get_logger
from shared.service_heartbeat import get_instance_name, start_heartbeat_task, stop_heartbeat_task
from .worker import DownloaderWorker

logger = get_logger('media_downloader')
SERVICE_TYPE = 'downloader'
INSTANCE_NAME = get_instance_name('downloader-1')


async def main() -> None:
    worker = DownloaderWorker()
    task = asyncio.create_task(worker.run())
    queue_start_position = int(os.getenv('QUEUE_START_POSITION', '0'))
    heartbeat_task = start_heartbeat_task(
        SERVICE_TYPE,
        INSTANCE_NAME,
        queue_start_position=queue_start_position,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    logger.info('media_downloader service started')
    await stop_event.wait()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await stop_heartbeat_task(heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)

    logger.info('media_downloader service stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')
