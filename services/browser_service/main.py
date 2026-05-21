import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.logger import get_logger
from shared.service_heartbeat import (
    get_instance_name,
    start_heartbeat_task,
    stop_heartbeat_task,
    update_service_heartbeat,
)
from .browser_manager import BrowserManager
from .session_manager import save_cookies

logger = get_logger('browser_service')
app = FastAPI()
manager = BrowserManager()

HEALTH_CHECK_INTERVAL = 60
FAILURE_THRESHOLD = 3
SERVICE_TYPE = 'browser_service'
INSTANCE_NAME = get_instance_name('browser_service_main')
BROWSER_STARTUP_TIMEOUT_SECONDS = int(os.getenv('BROWSER_STARTUP_TIMEOUT_SECONDS', '120'))


class ManualLoginRequest(BaseModel):
    account_id: int


async def _update_service_instance() -> None:
    await update_service_heartbeat(SERVICE_TYPE, INSTANCE_NAME)


async def _start_browser_session(app: FastAPI, account_id: int) -> None:
    try:
        await asyncio.wait_for(manager.start(account_id), timeout=BROWSER_STARTUP_TIMEOUT_SECONDS)
        app.state.health_task = asyncio.create_task(_health_check_loop(account_id))
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.error(
            'Browser startup timed out for account %s after %s seconds',
            account_id,
            BROWSER_STARTUP_TIMEOUT_SECONDS,
        )
        manager.login_status = 'error'
        manager.login_status_message = 'Browser startup timed out; manual Instagram login is required'
        await manager.close()
    except Exception as exc:
        logger.exception('Browser startup failed for account %s: %s', account_id, exc)
        manager.login_status = 'error'
        manager.login_status_message = str(exc)
        await manager.close()


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

            await _update_service_instance()
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info('Health check loop cancelled')
            break
        except Exception as exc:
            logger.exception('Health check loop error: %s', exc)
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.heartbeat_task = start_heartbeat_task(SERVICE_TYPE, INSTANCE_NAME)
    account_id_value = os.getenv('INSTAGRAM_ACCOUNT_ID')
    if not account_id_value:
        logger.warning('INSTAGRAM_ACCOUNT_ID is not set; browser service will wait for manual session requests')
        app.state.account_id = None
        app.state.last_check = None
        app.state.health_task = None
        yield
        await stop_heartbeat_task(app.state.heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)
        await manager.close()
        return

    try:
        account_id = int(account_id_value)
    except ValueError:
        raise RuntimeError('INSTAGRAM_ACCOUNT_ID must be an integer')

    app.state.account_id = account_id
    app.state.last_check = None
    app.state.health_task = None
    manager.login_status = 'starting'
    manager.login_status_message = 'Browser startup is running'
    app.state.startup_task = asyncio.create_task(_start_browser_session(app, account_id))

    yield

    startup_task = getattr(app.state, 'startup_task', None)
    if startup_task and not startup_task.done():
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass

    if app.state.health_task:
        app.state.health_task.cancel()
    await stop_heartbeat_task(app.state.heartbeat_task, SERVICE_TYPE, INSTANCE_NAME)
    await manager.close()


app.router.lifespan_context = lifespan


@app.get('/health')
async def health():
    account_id = getattr(app.state, 'account_id', None)
    if manager.login_status in ('starting', 'error'):
        session_alive = False
    else:
        session_alive = await manager.check_session()
    return JSONResponse({
        'status': 'ok',
        'account_id': account_id,
        'session_alive': session_alive,
        'login_status': manager.login_status,
        'login_status_message': manager.login_status_message,
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


@app.post('/session/start-manual-login')
async def start_manual_login(payload: ManualLoginRequest):
    await manager.start_manual_login(payload.account_id)
    app.state.account_id = payload.account_id
    return JSONResponse({'status': 'browser_opened'})


@app.post('/session/confirm-login')
async def confirm_login():
    success = await manager.confirm_login()
    return JSONResponse({
        'success': success,
        'status': manager.get_login_status().get('status'),
    })


@app.get('/session/login-status')
async def login_status():
    return JSONResponse(manager.get_login_status())


@app.post('/restart')
async def restart_service():
    await manager.restart()
    return JSONResponse({'restarted': True})


if __name__ == '__main__':
    import uvicorn

    logger.info('Starting browser service server')
    uvicorn.run(
        'services.browser_service.main:app',
        host='0.0.0.0',
        port=8000,
        reload=False,
        log_level='info',
    )
