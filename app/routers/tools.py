from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..models import User

router = APIRouter(prefix="/tools", tags=["工具平台"])


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """工具平台受保护端点：与商业平台共享同一登录态（SSO）。"""
    return {"platform": "tools", "message": "pong"}
