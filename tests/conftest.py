import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根加入 sys.path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models import OAuthClient
from app.security import hash_password
from main import app

# 测试默认关掉限流：所有用例共用同一个客户端 IP，开着的话几十个注册/登录会互相挤爆配额。
# 限流本身由 tests/test_ratelimit.py 显式打开后测试（见 HANDOVER 的坑）。
settings.RATE_LIMIT_ENABLED = False

# 测试库默认是内存 SQLite（StaticPool 保证所有连接共享同一内存库）。
# 设 TEST_DATABASE_URL 就能整套跑在真 PostgreSQL 上，用来消掉「集成测试只跑 SQLite」
# 这个上线阻塞项（TECH_DECISIONS.md TD-80）：
#   TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" \
#     .venv/bin/python -m pytest -q
# 注意别指向正在用的业务库 —— fixture 每个用例都会 create_all / drop_all。
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite://")

if TEST_DATABASE_URL.startswith("sqlite"):
    engine = create_async_engine(
        TEST_DATABASE_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
else:
    # 真库必须用 NullPool：asyncpg 的连接绑死在创建它的事件循环上，而 pytest-asyncio
    # 每个用例开一个新 loop。用默认连接池会复用到上一个 loop 的连接，报
    # "got Future attached to a different loop"。SQLite 那边因为是 StaticPool
    # 单连接才没暴露这个问题。
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
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
