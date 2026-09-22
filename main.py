import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from app import cpu_pool
from app.config import settings
from app.database import engine
from app.middleware import (
    RequestBodyBudgetMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    validation_error_without_input,
)
from app.routers import (
    admin,
    auth,
    diagrams,
    health,
    messages,
    oauth,
    payments_admin,
    refund_notify,
    refunds_admin,
    shop,
    site,
    support,
    tools,
)
from app.startup_checks import enforce_database_safety, enforce_production_settings
from app.tools.faq import warm_semantic_index

# 依赖模块导入后、应用实例装配前配置 logging；uvicorn 也会配置日志，这里只设级别与格式，
# 不去动它的 handler，避免两边打架（TD-165）。
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# 生产配置不合规就拒绝启动 —— 等到用户下单时才发现就晚了（见 app/startup_checks.py）
enforce_production_settings()

@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        # Production checks DB/journal/known demo identities before accepting requests; no auto-migration.
        await enforce_database_safety()
        # Embedding remains optional and off by default; warm-up is not a production readiness proof.
        await warm_semantic_index()
        yield
    finally:
        # Also clean up if startup fails before yield.
        cpu_pool.shutdown()
        await engine.dispose()



app = FastAPI(
    title="codemax_platform", version="0.1.0", lifespan=lifespan,
    docs_url=None if settings.ENV == "production" else "/docs",
    redoc_url=None if settings.ENV == "production" else "/redoc",
    openapi_url=None if settings.ENV == "production" else "/openapi.json",
)

# 中间件是**后加先执行**（洋葱模型），所以日志放最后加，让它包在最外层，
# 这样连安全头中间件自己的耗时也算进去，且异常也能被记录到。
# 请求体预算最先加 = 最靠近路由：它发出的 413 同样经过安全头与日志两层。
app.add_middleware(RequestBodyBudgetMiddleware)
# 响应压缩（TD-270）：Mermaid 页的产物 16 个文件共 669 KiB，gzip 后 167 KiB；本地/直连（Windows 指南、
# 无 nginx）场景下由应用自己压。starlette 自带，无新依赖；只压 ≥ 1 KiB 的响应，且客户端声明 gzip 才压。
# 放在安全头中间件之内、路由之外：压缩后的响应照样带安全头与日志。BREACH 只影响"响应里混有攻击者可控
# 输入 + 秘密"的动态页；本站 JSON/HTML 响应不回显 CSRF 令牌之类的秘密（会话在 HttpOnly Cookie 里，不在正文）。
# 交付物走 application/octet-stream：不在 starlette 的默认排除表里，但 ZIP 再压一遍只费 CPU、
# 还会丢掉 Content-Length（浏览器没进度条），所以显式排除；Range（206）请求 starlette 本来就不压。
app.add_middleware(GZipMiddleware, minimum_size=1024,
                   exclude_content_types=(*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream"))
app.add_middleware(SecurityHeadersMiddleware, hsts_max_age=settings.HSTS_MAX_AGE)
app.add_middleware(RequestLoggingMiddleware)
# 422 只回 type/loc/msg/ctx，不把出错字段的原值（可能几十万字符）整个回显（TD-260）。
app.add_exception_handler(RequestValidationError, validation_error_without_input)

app.include_router(health.router)  # S5-03-3：存活/就绪探针
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(tools.router)
app.include_router(diagrams.router)  # S2-01-3：Drawio 流程图存取（需鉴权）
app.include_router(shop.router)
app.include_router(payments_admin.router)
app.include_router(refunds_admin.router)
app.include_router(refund_notify.router)
app.include_router(site.router)  # S2-02-1：页面 SSR + sitemap + robots
app.include_router(messages.router)
app.include_router(support.router)  # S4-02：智能客服三层
app.include_router(admin.router)  # TD-138：管理员抓取入库（S4-01-4 的 HTTP 入口）

# 工具平台前端静态资源（Vite 页面入口和分块等）；HTML 页面走 Jinja2 SSR，见 app/routers/site.py
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "app" / "static", html=True),
    name="static",
)
