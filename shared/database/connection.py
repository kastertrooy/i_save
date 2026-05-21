from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from shared.config import DATABASE_URL
from shared.database.models import Base

# Создание асинхронного движка SQLAlchemy 2.x
engine = create_async_engine(DATABASE_URL, echo=False)  # Установите echo=True для отладки

# Создание фабрики сессий
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Dependency для FastAPI: предоставляет AsyncSession
async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


# Функция для инициализации базы данных (создание таблиц при первом запуске)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)