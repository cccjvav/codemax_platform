"""Drawio 流程图存取（S2-01-3）。

流程图是用户私有资产，与 ER/Mermaid 那类公开引流工具语义不同：**全部端点需鉴权**，
且只能读写自己的记录。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import get_current_user
from ..models import SysDiagram, User
from ..schemas import DiagramIn, DiagramOut, DiagramSummary

router = APIRouter(prefix="/diagrams", tags=["流程图（Drawio）"])


async def _owned(db: AsyncSession, user: User, diagram_id: int) -> SysDiagram:
    diagram = await db.scalar(
        select(SysDiagram).where(SysDiagram.id == diagram_id, SysDiagram.user_id == user.id)
    )
    if not diagram:
        # "不存在"与"不是你的"返回同一个 404，避免探测他人资源是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, "流程图不存在")
    return diagram


@router.get("", response_model=list[DiagramSummary])
async def list_diagrams(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(SysDiagram).where(SysDiagram.user_id == user.id).order_by(SysDiagram.update_time.desc())
    )
    return rows.all()


@router.post("", response_model=DiagramOut, status_code=201)
async def create_diagram(
    data: DiagramIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
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
    await db.delete(await _owned(db, user, diagram_id))
    await db.commit()
