from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import User
from ..schemas import ErDiagramIn
from ..tools.sql_ddl import parse_ddl

router = APIRouter(prefix="/tools", tags=["工具平台"])


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """工具平台受保护端点：与商业平台共享同一登录态（SSO）。"""
    return {"platform": "tools", "message": "pong"}


@router.post("/er-diagram")
async def er_diagram(data: ErDiagramIn) -> dict:
    """S2-01-1：解析 SQL DDL，返回 D3.js 可直接渲染的 ER 图数据。

    引流工具，故不设鉴权（便于 SEO 收录与游客直接使用）。
    """
    graph = parse_ddl(data.ddl)
    if not graph["tables"]:
        raise HTTPException(400, "未解析到任何 CREATE TABLE 语句")
    return graph
