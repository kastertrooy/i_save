import asyncio
import os
import signal

from shared.logger import get_logger
from services.browser_service.browser_manager import BrowserManager
from .watcher import InstagramWatcher

logger = get_logger('instagram_watcher')


async def main() -> None:
    account_id_value = os.getenv('INSTAGRAM_ACCOUNT_ID')
    if not account_id_value:
        raise RuntimeError('INSTAGRAM_ACCOUNT_ID environment variable is required')

    try:
        account_id = int(account_id_value)
    except ValueError:
        raise RuntimeError('INSTAGRAM_ACCOUNT_ID must be an integer')

    browser_manager = BrowserManager()
    watcher = InstagramWatcher(browser_manager)

    async def shutdown() -> None:
        logger.info('Shutting down instagram_watcher...')
        await watcher.stop()
        await browser_manager.close()
        logger.info('instagram_watcher stopped')

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_exit() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_exit)
        except NotImplementedError:
            pass

    await watcher.start(account_id)
    logger.info('instagram_watcher running for account %s', account_id)

    await stop_event.wait()
    await shutdown()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Received KeyboardInterrupt, exiting...')
