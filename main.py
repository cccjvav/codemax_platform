from fastapi import FastAPI

from app.routers import auth, shop, tools

app = FastAPI(title="codemax_platform", version="0.1.0")
app.include_router(auth.router)
app.include_router(tools.router)
app.include_router(shop.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
