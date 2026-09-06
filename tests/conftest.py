import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根加入 sys.path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models import OAuthClient
from app.security import hash_password
from app.storage import LocalStorage
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

def iter_app_routes(routes):
    """把 `app.routes` 递归展平成一条条真实路由。

    ## 为什么需要它

    **FastAPI 0.141 改了 `include_router` 的行为**：不再把子路由摊平进
    `app.routes`，而是塞一个 `_IncludedRouter` 包装对象进去。实测升级后
    `{getattr(r, "path", None) for r in app.routes}` 只剩
    `{'/docs', '/openapi.json', '/redoc', '/static', None, ...}` ——
    业务路由一个都没有，10 处靠遍历 `app.routes` 断言的测试当场全红。

    ## 为什么不直接读 `_IncludedRouter`

    它是 `fastapi.routing` 里的**私有类**，名字和结构都可能再变。
    这里只用它公开的 `original_router` 属性拿回被包含的那个 router，
    并且**递归**处理（router 里还能再 include router）。

    ## 为什么写成版本无关

    `getattr(r, "original_router", None)` 在旧版 FastAPI 上恒为 None，
    于是直接 yield 原对象 —— 同一份代码在新旧两版上都给出正确结果。
    这样万一有人把 FastAPI 降回去，这批测试不会反过来变红。

    生产代码不受此变更影响：`scripts/build_docs_site.py` 是用 `ast`
    解析 `@router.*` 装饰器统计路由的，不碰运行期的 `app.routes`。
    """
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from iter_app_routes(inner.routes)
        else:
            yield r


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


# ---------------------------------------------------------------- SSO 授权流程辅助
# TD-78 之后 GET /oauth/authorize 只渲染同意页、不签发授权码，签发在 POST。
# 所以「拿到一个 code」必须走两步：GET 取同意页 → 从隐藏表单里抠出 sig → POST 提交。
# 这也正是浏览器真实做的事，测试跟着走一遍才不会把签名校验测成摆设。
_SIG_RE = re.compile(r'name="sig" value="([^"]+)"')


async def sso_authorize(
    client,
    headers,
    *,
    client_id="tools",
    redirect_uri="https://tools.codemax.top/callback",
    state="xyz",
    approve="1",
):
    """走完整的同意流程，返回**POST** 的响应（302，Location 里带 code）。"""
    page = await client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": client_id,
                "redirect_uri": redirect_uri, "state": state},
        headers=headers,
        follow_redirects=False,
    )
    assert page.status_code == 200, f"同意页应返回 200，实际 {page.status_code}：{page.text[:200]}"
    m = _SIG_RE.search(page.text)
    assert m, "同意页缺少签名字段"
    return await client.post(
        "/oauth/authorize",
        data={"client_id": client_id, "redirect_uri": redirect_uri,
              "state": state, "sig": m.group(1), "approve": approve},
        headers=headers,
        follow_redirects=False,
    )


def sso_code(response) -> str:
    """从 302 的 Location 里取出 code。"""
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]

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


@pytest_asyncio.fixture
async def db():
    """一个能直接用的 AsyncSession（建表 → yield → 拆表）。

    从 test_support.py 上移到这里：S4-02-5 的 test_intent_cascade.py 也要用它。
    与 `client` 的区别是它**不起 HTTP 层**，适合直接测 tools/ 里的业务函数。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------- 共享 fixture
#
# 商品文件桩。**放在 conftest 而不是 test_download.py**：S2-02-2 的
# test_shop_page.py 也要用它（测「轮询订单状态不会烧掉一次性下载」时，
# 必须真能发出下载链接才测得出来）。
# 跨模块 `from tests.test_download import product` 再当形参会撞 ruff F811
# （形参名遮蔽导入名），而共享 fixture 的正确位置本来就是 conftest。

PRODUCT_KEY = "product/codemax_package.zip"
PRODUCT_BYTES = b"PK\x03\x04 " + "这是商品文件的内容".encode()


@pytest.fixture
def mock_mode(monkeypatch):
    """走模拟收银台（TD-124）。

    ⚠️ 打 `/shop/orders` 的测试**必须**带这个 fixture：默认 `SHOP_PAY_MODE=wechat`
    而沙箱没有商户号，下单会直接 503，很容易被误判成代码 bug。
    """
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")

    from app.routers import shop  # 局部 import：conftest 不该在模块级拉路由

    async def no_wechat(cfg, **kw):
        raise AssertionError("模拟模式不该调用微信支付")

    monkeypatch.setattr(shop, "native_prepay", no_wechat)



@pytest.fixture
def product(tmp_path, monkeypatch):
    """把存储后端指到临时目录，并造出商品文件。"""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_PRODUCT_KEY", PRODUCT_KEY)
    LocalStorage(str(tmp_path), "http://test", settings.SECRET_KEY).put(PRODUCT_KEY, PRODUCT_BYTES)
    return tmp_path
