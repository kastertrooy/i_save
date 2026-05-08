import asyncio
import signal

from shared.logger import get_logger
from .worker import MediaDownloaderWorker

logger = get_logger('media_downloader')


async def main() -> None:
    worker = MediaDownloaderWorker()
    task = asyncio.create_task(worker.run())

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

    logger.info('media_downloader service stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')