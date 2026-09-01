from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import auth, diagrams, oauth, shop, site, support, tools

app = FastAPI(title="codemax_platform", version="0.1.0")
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(tools.router)
app.include_router(diagrams.router)  # S2-01-3：Drawio 流程图存取（需鉴权）
app.include_router(shop.router)
app.include_router(site.router)  # S2-02-1：页面 SSR + sitemap + robots
app.include_router(support.router)  # S4-02：智能客服三层

# 工具平台前端静态资源（er.js 等）；HTML 页面走 Jinja2 SSR，见 app/routers/site.py
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "app" / "static", html=True),
    name="static",
)


@app.get("/health")
async def health():
    return {"status": "ok"}
