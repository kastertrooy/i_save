import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from shared.database.connection import async_session
from shared.database.models import ServiceInstance
from shared.logger import get_logger
from .browser_manager import BrowserManager
from .session_manager import save_cookies

logger = get_logger('browser_service')
app = FastAPI()
manager = BrowserManager()

HEALTH_CHECK_INTERVAL = 60
FAILURE_THRESHOLD = 3
SERVICE_TYPE = 'browser_service'
INSTANCE_NAME = 'browser_service_main'


async def _update_service_instance(last_heartbeat_at: datetime) -> None:
    async with async_session() as session:
        stmt = await session.execute(
            ServiceInstance.__table__.select().where(
                ServiceInstance.service_type == SERVICE_TYPE,
                ServiceInstance.instance_name == INSTANCE_NAME,
            )
        )
        instance = stmt.first()
        if instance:
            await session.execute(
                ServiceInstance.__table__.update()
                .where(
                    ServiceInstance.service_type == SERVICE_TYPE,
                    ServiceInstance.instance_name == INSTANCE_NAME,
                )
                .values(last_heartbeat_at=last_heartbeat_at)
            )
        else:
            await session.execute(
                ServiceInstance.__table__.insert().values(
                    service_type=SERVICE_TYPE,
                    instance_name=INSTANCE_NAME,
                    status='running',
                    last_heartbeat_at=last_heartbeat_at,
                    queue_start_position=None,
                )
            )
        await session.commit()


async def _health_check_loop(account_id: int) -> None:
    failure_count = 0
    while True:
        try:
            session_alive = await manager.check_session()
            last_check = datetime.utcnow()

            if not session_alive:
                failure_count += 1
                logger.warning('Session check failed (%s/%s)', failure_count, FAILURE_THRESHOLD)
                if failure_count >= FAILURE_THRESHOLD:
                    logger.warning('Failure threshold reached, restarting browser...')
                    await manager.restart()
                    failure_count = 0
            else:
                failure_count = 0

            app.state.last_check = last_check
            try:
                cookies = await manager.context.cookies() if manager.context else []
                if cookies:
                    await save_cookies(account_id, cookies)
            except Exception as exc:
                logger.warning('Failed to save cookies during health check: %s', exc)

            await _update_service_instance(last_check)
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info('Health check loop cancelled')
            break
        except Exception as exc:
            logger.exception('Health check loop error: %s', exc)
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    account_id_value = os.getenv('INSTAGRAM_ACCOUNT_ID')
    if not account_id_value:
        raise RuntimeError('INSTAGRAM_ACCOUNT_ID environment variable is required')
    try:
        account_id = int(account_id_value)
    except ValueError:
        raise RuntimeError('INSTAGRAM_ACCOUNT_ID must be an integer')

    await manager.start(account_id)
    app.state.account_id = account_id
    app.state.last_check = None
    app.state.health_task = asyncio.create_task(_health_check_loop(account_id))

    yield

    app.state.health_task.cancel()
    await manager.close()


@app.get('/health')
async def health():
    account_id = getattr(app.state, 'account_id', None)
    session_alive = await manager.check_session()
    return JSONResponse({
        'status': 'ok',
        'account_id': account_id,
        'session_alive': session_alive,
    })


@app.get('/session/status')
async def session_status():
    logged_in = await manager.check_session()
    last_check = getattr(app.state, 'last_check', None)
    return JSONResponse({
        'logged_in': logged_in,
        'last_check': last_check.isoformat() if last_check else None,
    })


@app.post('/session/save')
async def session_save():
    if not manager.context:
        raise HTTPException(status_code=500, detail='Browser context is not available')
    account_id = getattr(app.state, 'account_id', None)
    if account_id is None:
        raise HTTPException(status_code=500, detail='Account ID is not configured')

    cookies = await manager.context.cookies()
    await save_cookies(account_id, cookies)
    return JSONResponse({'saved': True})


@app.post('/restart')
async def restart_service():
    await manager.restart()
    return JSONResponse({'restarted': True})
