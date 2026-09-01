from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.sqlalchemy_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    async with SessionLocal() as session:
        yield session
