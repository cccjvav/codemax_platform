import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根加入 sys.path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import OAuthClient
from app.security import hash_password
from main import app

# 测试用内存 SQLite（StaticPool 保证所有连接共享同一内存库）
engine = create_async_engine(
    "sqlite+aiosqlite://",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
TestSession = async_sessionmaker(engine, expire_on_commit=False)

# 测试种子：两个 SSO 接入平台（与 database init/full_init.sql 一致）
# 注意：必须每次调用新建实例，否则 ORM 对象跨测试复用会泄漏状态
def seed_clients() -> list[OAuthClient]:
    return [
        OAuthClient(
            client_id="tools",
            client_secret_hash=hash_password("codemax-tools-secret"),
            name="工具平台",
            redirect_uri="https://tools.codemax.top/callback",
        ),
        OAuthClient(
            client_id="shop",
            client_secret_hash=hash_password("codemax-shop-secret"),
            name="商业平台",
            redirect_uri="https://shop.codemax.top/callback",
        ),
    ]


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as session:
        session.add_all(seed_clients())
        await session.commit()

    async def override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
