import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import cpu_pool
from app.config import settings
from app.database import engine
from app.middleware import RequestBodyBudgetMiddleware, RequestLoggingMiddleware, SecurityHeadersMiddleware
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
# 请求体预算最先加（最内层）：它自己发出的 413/408 也要经过安全头并被日志记录。
app.add_middleware(RequestBodyBudgetMiddleware)
app.add_middleware(SecurityHeadersMiddleware, hsts_max_age=settings.HSTS_MAX_AGE)
app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error_without_echo(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 只返回位置、消息和类型，不回显 `input`/`ctx`（A-01）。

    默认处理器会把整个出错字段原样放回响应：一个刚好卡在预算内的 60 KB 文本换来 60 KB 应答，
    对匿名可达的 /tools、/support 是放大器；`ctx` 里可能带异常原文，同样不外泄。
    """
    detail = [{k: err.get(k) for k in ("loc", "msg", "type")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})

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
