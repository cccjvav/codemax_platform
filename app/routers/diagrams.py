"""Drawio 流程图存取（S2-01-3）。

流程图是用户私有资产，与 ER/Mermaid 那类公开引流工具语义不同：**全部端点需鉴权**，
且只能读写自己的记录。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import SysDiagram, User
from ..schemas import DiagramIn, DiagramOut, DiagramSummary

router = APIRouter(prefix="/diagrams", tags=["流程图（Drawio）"])


def _alive():
    """「存活」条件。集中在一处，避免某个查询忘了过滤软删除的行。"""
    return SysDiagram.deleted_at.is_(None)


async def _owned(db: AsyncSession, user: User, diagram_id: int, *, include_deleted: bool = False) -> SysDiagram:
    """取自己的流程图。`include_deleted` 只有恢复端点会打开。"""
    stmt = select(SysDiagram).where(SysDiagram.id == diagram_id, SysDiagram.user_id == user.id)
    if not include_deleted:
        stmt = stmt.where(_alive())
    diagram = await db.scalar(stmt)
    if not diagram:
        # "不存在"、"不是你的"、"已删除"返回同一个 404，避免探测他人资源是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, "流程图不存在")
    return diagram


async def _live_count(db: AsyncSession, user: User) -> int:
    return await db.scalar(
        select(func.count()).select_from(SysDiagram).where(SysDiagram.user_id == user.id, _alive())
    )


@router.get("", response_model=list[DiagramSummary])
async def list_diagrams(
    deleted: bool = False, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """自己的流程图。`deleted=true` 列回收站（恢复之前得先看得见）。

    用查询参数而不是 `/diagrams/trash` 子路径：后者会被先注册的 `/{diagram_id}` 吃掉。
    """
    stmt = select(SysDiagram).where(SysDiagram.user_id == user.id)
    stmt = stmt.where(SysDiagram.deleted_at.is_not(None) if deleted else _alive())
    rows = await db.scalars(stmt.order_by(SysDiagram.update_time.desc()))
    return rows.all()


@router.post("", response_model=DiagramOut, status_code=201)
async def create_diagram(
    data: DiagramIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    # 每用户配额（TD-64）：不设的话任何人都能无限建图把库刷满。
    # 只数存活行，所以删掉一张就腾出一个名额。
    if await _live_count(db, user) >= settings.DIAGRAM_QUOTA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"流程图数量已达上限（{settings.DIAGRAM_QUOTA} 张），请先删除一些再新建",
        )
    diagram = SysDiagram(user_id=user.id, name=data.name, content=data.content)
    db.add(diagram)
    await db.commit()
    await db.refresh(diagram)
    return diagram


@router.get("/{diagram_id}", response_model=DiagramOut)
async def get_diagram(
    diagram_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await _owned(db, user, diagram_id)


@router.put("/{diagram_id}", response_model=DiagramOut)
async def update_diagram(
    diagram_id: int,
    data: DiagramIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    diagram = await _owned(db, user, diagram_id)
    diagram.name = data.name
    diagram.content = data.content
    await db.commit()
    await db.refresh(diagram)
    return diagram


@router.delete("/{diagram_id}", status_code=204)
async def delete_diagram(
    diagram_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """删除 = 打软删除时间戳（TD-64），可以用 POST /{id}/restore 恢复。

    对调用方而言与硬删除无异：删完 GET 就是 404、列表里也没有。
    """
    diagram = await _owned(db, user, diagram_id)
    diagram.deleted_at = datetime.now(timezone.utc)  # 列是 TIMESTAMPTZ（TD-146 的约定）
    await db.commit()


@router.post("/{diagram_id}/restore", response_model=DiagramOut)
async def restore_diagram(
    diagram_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """从回收站恢复。

    恢复要占配额 —— 否则「建满 → 删 → 恢复」就能绕过上限。
    """
    diagram = await _owned(db, user, diagram_id, include_deleted=True)
    if diagram.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该流程图没有被删除")
    if await _live_count(db, user) >= settings.DIAGRAM_QUOTA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"恢复失败：存活流程图已达上限（{settings.DIAGRAM_QUOTA} 张）",
        )
    diagram.deleted_at = None
    await db.commit()
    await db.refresh(diagram)
    return diagram
