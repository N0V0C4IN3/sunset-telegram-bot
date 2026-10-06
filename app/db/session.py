from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)

