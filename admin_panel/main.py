from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared.database.connection import init_db
from shared.logger import get_logger
from admin_panel.routers import (
    auth, dashboard, pages, services, users,
    accounts, groups, staff, settings, logs
)

logger = get_logger('admin_panel')


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Lifespan context manager for startup and shutdown."""
    # Startup
    logger.info('Admin panel starting up')
    await init_db()
    logger.info('Database initialized')
    
    yield
    
    # Shutdown
    logger.info('Admin panel shutting down')


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title='INSTATG Admin Panel',
        description='Administration panel for INSTATG bot',
        version='1.0.0',
        lifespan=lifespan
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],  # In production, restrict to specific domains
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    
    # Mount static files
    static_dir = Path(__file__).resolve().parent / 'static'
    try:
        app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')
        logger.info('Static files mounted at /static')
    except Exception as e:
        logger.warning('Could not mount static files: %s', str(e))
    
    # Setup Jinja2 templates
    try:
        templates = Jinja2Templates(directory='admin_panel/templates')
        logger.info('Jinja2 templates loaded from admin_panel/templates')
    except Exception as e:
        logger.warning('Could not load templates: %s', str(e))
    
    # Include routers
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(services.router)
    app.include_router(users.router)
    app.include_router(accounts.router)
    app.include_router(groups.router)
    app.include_router(staff.router)
    app.include_router(settings.router)
    app.include_router(logs.router)
    
    logger.info('All routers registered')
    
    # Health check endpoint
    @app.get('/health')
    async def health_check() -> dict:
        """Health check endpoint."""
        return {'status': 'ok'}
    
    logger.info('Admin panel FastAPI app created successfully')
    return app


app = create_app()


if __name__ == '__main__':
    import uvicorn
    logger.info('Starting admin panel server')
    uvicorn.run(
        'admin_panel.main:app',
        host='0.0.0.0',
        port=8000,
        reload=True,
        log_level='info'
    )
