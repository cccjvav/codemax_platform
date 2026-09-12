from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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


async def lock_user(db: AsyncSession, user_id: int):
    """Serialize per-user mutations in PostgreSQL and SQLite; caller owns commit/rollback."""
    from sqlalchemy import select, update

    from .models import User

    await db.execute(update(User).where(User.id == user_id).values(update_time=User.update_time))
    return await db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
