import asyncio
import signal

from shared.logger import get_logger
from shared.service_heartbeat import get_instance_name, start_heartbeat_task, stop_heartbeat_task
from .worker import SenderWorker

logger = get_logger('media_sender')
SERVICE_TYPE = 'sender'
INSTANCE_NAME = get_instance_name('sender-1')


async def main() -> None:
    worker = SenderWorker()
    task = asyncio.create_task(worker.run())
    heartbeat_task = start_heartbeat_task(SERVICE_TYPE, INSTANCE_NAME)
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    logger.info('media_sender service started')
    try:
        while not stop_event.is_set():
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await stop_heartbeat_task(heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)
        logger.info('media_sender service stopped')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')
