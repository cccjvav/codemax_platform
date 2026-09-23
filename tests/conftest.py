import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根加入 sys.path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import security
from app.config import settings
from app.database import Base, get_db
from app.models import OAuthClient
from app.security import hash_password
from app.storage import LocalStorage
from main import app

# 测试默认关掉限流：所有用例共用同一个客户端 IP，开着的话几十个注册/登录会互相挤爆配额。
# 限流本身由 tests/test_ratelimit.py 显式打开后测试（见 HANDOVER 的坑）。
settings.RATE_LIMIT_ENABLED = False

# 测试进程把 bcrypt 成本降到 4（O-11 / TD-272）。
#
# 实测：全量套件里 bcrypt 的 hash/verify 共 3491 次、耗时 1030.9 s，占总时长 1276 s 的 81%；
# 降成本后全量 220 s（5.8×），结果仍是 1822 passed / 7 skipped。生产级成本 12 是**运行期**属性，
# 它值多少钱由真实攻击成本决定，不该由测试重复支付 —— 每个用例都付一遍 260 ms，
# 只是让 CI 逼近 35 分钟超时。真实哈希格式不变（仍是 `$2b$` 前缀，只因轮数变成 4 而更短）。
#
# 为什么只改这一个进程：CryptContext 只在导入时读默认轮数，改的是本进程内存里的策略；
# `app/security.py` 的模块级对象与生产启动路径不受影响。需要真实成本的用例
# （tests/test_auth_crypto.py 的两条时序/侧信道断言）自己在用例内恢复到生产值。
settings_rounds_production = security.pwd_context.handler("bcrypt").default_rounds
security.pwd_context.update(bcrypt__rounds=4)

# 沙箱与 CI 都没有真实模型 key。大多数用例走 dependency_overrides 注入假客户端；
# 少数直接打路由的用例（配额、路由形状）会拿到 `default_llm`，于是拿到的是
# 「未配置 LLM_API_KEY」这条本地配置错误，而不是它们真正想验证的上游错误。
# 这里给一个**明显是假的**占位 key，让 `chat()` 真的走到 httpx 层，由测试自己的
# transport 决定结果；它不会让任何真实请求被发出（没有 base_url 可达性声明）。
# 用例若要断言「未配置 key」的行为，用 monkeypatch 删掉它。
settings.LLM_API_KEY = settings.LLM_API_KEY or "test-placeholder-key-not-a-credential"

# Default tests use a disposable SQLite file and independent connections. StaticPool's single
# connection interleaves unrelated sessions/rollbacks and cannot validate settlement transactions.
# Explicit TEST_DATABASE_URL is still reserved for disposable databases, never business data.
_SQLITE_TEMP = tempfile.TemporaryDirectory(prefix='codemax-tests-')
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_SQLITE_TEMP.name}/tests.db")
engine = create_async_engine(
    TEST_DATABASE_URL, poolclass=NullPool,
    **({'connect_args': {'timeout': 30}} if TEST_DATABASE_URL.startswith('sqlite') else {}),
)
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


# 测试种子：两个 SSO 接入平台（对应显式seed_demo.sql；生产init不包含这些身份）
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
async def client(product):
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
# test_shop_page.py 也要用它（测「轮询订单状态不会申请下载链接」时，
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


@pytest.fixture
def product_file(tmp_path, monkeypatch):
    """同上，但**直接落在 settings.STORAGE_LOCAL_ROOT 的默认值**（相对路径 `storage/`）下。

    为什么需要第二个商品桩：`product` 把根目录改成 tmp_path，于是默认相对根
    （`storage/`，相对**进程工作目录**）在测试里从没被解析过。真实部署里它才是
    「没配 .env 时商品放在哪」的答案，而 Windows 指南第 8 步也要求把 ZIP 放进
    `storage/product/`。这个 fixture 让「相对根能不能被正确解析」变成可回归的断言，
    而不是只能靠人在另一台机器上手测。
    """
    root = Path(settings.STORAGE_LOCAL_ROOT)
    assert not root.is_absolute(), "前提校验：默认根应当是相对路径，否则这条回归失去意义"
    monkeypatch.chdir(tmp_path)  # 相对根按运行目录解析；测试里把「运行目录」固定在临时目录
    (tmp_path / PRODUCT_KEY).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / PRODUCT_KEY).write_bytes(PRODUCT_BYTES)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_PRODUCT_KEY", PRODUCT_KEY)
    return tmp_path / PRODUCT_KEY


def project_ddl_for_api() -> str:
    """Full project table DDL under unchanged public input budget; not a migration script.

    Ignore procedural maintenance statements unsupported by the ER graph. Exact graph equality
    against the original source prevents silently dropping fields, tables, FKs or supported comments.
    """
    from app.tools.sql_ddl import _iter_tables, _strip_comments, parse_ddl
    raw = (Path(__file__).resolve().parents[1] / 'database init/full_init.sql').read_text(encoding='utf-8')
    ddl = '\n'.join(f'CREATE TABLE {name} ({body});' for name, body in _iter_tables(_strip_comments(raw)))
    assert parse_ddl(ddl) == parse_ddl(raw)
    return ddl
