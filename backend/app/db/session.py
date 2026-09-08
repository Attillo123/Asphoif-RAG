from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings

_engine: AsyncEngine | None = None


def _async_database_url(database_url: str) -> str:
    """Adapt the documented PyMySQL URL for SQLAlchemy's async engine."""
    if database_url.startswith("mysql+pymysql://"):
        return database_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if database_url.startswith("mysql://"):
        return database_url.replace("mysql://", "mysql+aiomysql://", 1)
    return database_url


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _async_database_url(settings.database_url),
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine has not been initialized")
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
