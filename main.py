import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import cpu_pool
from app.config import settings
from app.database import engine
from app.middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware
from app.routers import admin, auth, diagrams, health, oauth, shop, site, support, tools
from app.startup_checks import enforce_production_settings
from app.tools.faq import warm_semantic_index

# 日志配置放在导入应用之前：uvicorn 自己也会配 logging，这里只设定级别与格式，
# 不去动它的 handler，避免两边打架（TD-165）。
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# 生产配置不合规就拒绝启动 —— 等到用户下单时才发现就晚了（见 app/startup_checks.py）
enforce_production_settings()

@asynccontextmanager
async def lifespan(_: FastAPI):
    # 语义 FAQ 索引预热（S4-02-5）：把 12 条 FAQ 向量化一次缓存住。
    # **best-effort** —— 没配 LLM_API_KEY、网络不通、模型不支持中文，任何一条都只
    # 意味着退回词袋检索，绝不让启动失败。
    #
    # 刻意 await 而不是丢后台任务：后台任务可能晚于第一个请求才写完全局变量，
    # 那样同一个问题会出现「有时走语义、有时走词袋」的随机行为，极难排查。
    await warm_semantic_index()
    yield
    cpu_pool.shutdown()  # 回收进程池子进程，否则会留下孤儿进程
    # 干净归还数据库连接。少了这一句，进程被 SIGTERM 时池里的连接是**硬断开**的 ——
    # PostgreSQL 那边会留下一堆悬挂连接直到超时才回收。滚动发布频繁时，
    # 这些连接会把 max_connections 吃满，新副本反而起不来。
    await engine.dispose()


app = FastAPI(title="codemax_platform", version="0.1.0", lifespan=lifespan)

# 中间件是**后加先执行**（洋葱模型），所以日志放最后加，让它包在最外层，
# 这样连安全头中间件自己的耗时也算进去，且异常也能被记录到。
app.add_middleware(SecurityHeadersMiddleware, hsts_max_age=settings.HSTS_MAX_AGE)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)  # S5-03-3：存活/就绪探针
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(tools.router)
app.include_router(diagrams.router)  # S2-01-3：Drawio 流程图存取（需鉴权）
app.include_router(shop.router)
app.include_router(site.router)  # S2-02-1：页面 SSR + sitemap + robots
app.include_router(support.router)  # S4-02：智能客服三层
app.include_router(admin.router)  # TD-138：管理员抓取入库（S4-01-4 的 HTTP 入口）

# 工具平台前端静态资源（er.js 等）；HTML 页面走 Jinja2 SSR，见 app/routers/site.py
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "app" / "static", html=True),
    name="static",
)
