"""数据库连接：PostgreSQL（SQLAlchemy async）+ MongoDB（motor）。"""
from __future__ import annotations

from typing import AsyncGenerator

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_settings = get_settings()

# PostgreSQL：asyncpg 驱动
DATABASE_URL = (
    f"postgresql+asyncpg://{_settings.postgres_user}:{_settings.postgres_password}"
    f"@{_settings.postgres_host}:{_settings.postgres_port}/{_settings.postgres_db}"
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 请求级依赖：每个请求一个会话，成功时提交。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# MongoDB
_mongo_client: AsyncIOMotorClient | None = None


def get_mongo() -> AsyncIOMotorDatabase:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = AsyncIOMotorClient(_settings.mongo_uri)
    return _mongo_client.get_default_database()


async def close_mongo() -> None:
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None


async def dispose_engine() -> None:
    await engine.dispose()
