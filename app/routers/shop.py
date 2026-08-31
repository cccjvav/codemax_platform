from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..models import User

router = APIRouter(prefix="/shop", tags=["商业平台"])


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """商业平台受保护端点：与工具平台共享同一登录态（SSO）。"""
    return {"platform": "shop", "message": "pong"}
