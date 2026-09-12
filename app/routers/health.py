"""健康检查端点（S5-03-3）。

分两个，因为它们回答的是**两个不同的问题**，容器编排器对两者的处置也完全不同：

- `/healthz` 存活探针：进程还活着吗？不碰任何外部依赖。
  这里如果去查数据库，那么数据库一抖，编排器就会把**健康的**应用实例全部重启，
  把一次数据库故障放大成全站雪崩 —— 这是存活探针最经典的误用。
- `/readyz` 就绪探针：现在能接流量吗？会真的 ping 一次数据库。
  失败时编排器只是把实例从负载均衡里摘掉，等它恢复，不重启。

`/health` 保留为存活探针的别名：README 与既有测试都在用它，没必要为了统一命名
去改一圈文档（TD-164）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

router = APIRouter(tags=["运维"])


@router.get("/healthz", include_in_schema=False)
@router.get("/health", include_in_schema=False)
async def healthz():
    """存活探针：只证明进程能响应，不碰数据库。"""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(db: AsyncSession = Depends(get_db)):
    """就绪探针：真的执行一次 `SELECT 1`。库连不上就返回 503。"""
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=3.0)
    except Exception as e:  # 探针必须吞掉异常，否则探针自己会变成 500
        return Response(
            content=f'{{"status":"unavailable","reason":"database: {type(e).__name__}"}}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ready"}
