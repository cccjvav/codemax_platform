from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import auth, oauth, shop, tools

app = FastAPI(title="codemax_platform", version="0.1.0")
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(tools.router)
app.include_router(shop.router)

# 工具平台前端页面：/static/er.html（S2-01-1）
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "app" / "static", html=True),
    name="static",
)


@app.get("/health")
async def health():
    return {"status": "ok"}
