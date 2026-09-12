"""Drawio 流程图存取（S2-01-3）。

流程图是用户私有资产，与 ER/Mermaid 那类公开引流工具语义不同：**全部端点需鉴权**，
且只能读写自己的记录。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import LargeBinary, cast, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db, lock_user
from ..deps import get_current_user
from ..models import SysDiagram, User
from ..schemas import DiagramIn, DiagramOut, DiagramSummary

router = APIRouter(prefix="/diagrams", tags=["流程图（Drawio）"])


def _alive():
    """「存活」条件。集中在一处，避免某个查询忘了过滤软删除的行。"""
    return SysDiagram.deleted_at.is_(None)


async def _owned(db: AsyncSession, user: User, diagram_id: int, *, include_deleted: bool = False) -> SysDiagram:
    """查当前用户的文件；include_deleted 用于恢复及永久删除。不存在、非本人或默认排除的已删文件均 404。"""
    stmt = select(SysDiagram).where(SysDiagram.id == diagram_id, SysDiagram.user_id == user.id)
    if not include_deleted:
        stmt = stmt.where(_alive())
    diagram = await db.scalar(stmt)
    if not diagram:
        # "不存在"、"不是你的"、"已删除"返回同一个 404，避免探测他人资源是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, "流程图不存在")
    return diagram


def _etag(diagram: SysDiagram) -> str:
    """ETag 就是版本号，按 HTTP 规范加引号（强校验符）。"""
    return f'"{diagram.version}"'


def _parse_if_match(raw: str | None) -> int:
    """解析 If-Match。**缺失就报 428**（RFC 6585 Precondition Required），
    不默认放行 —— 默认放行等于这个接口仍然可以被静默覆盖。
    """
    if raw is None:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "缺少 If-Match 头：保存流程图必须带上你手上那一版的版本号（GET 响应的 ETag），"
            "否则并发编辑会互相覆盖",
        )
    tag = raw.strip().strip('"').strip()
    if not tag.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'If-Match 的值无法解析：{raw}')
    return int(tag)


async def _live_count(db: AsyncSession, user: User) -> int:
    return await db.scalar(
        select(func.count()).select_from(SysDiagram).where(SysDiagram.user_id == user.id, _alive())
    )


async def _storage_budget(db: AsyncSession, user_id: int, content: str, replacing: int | None = None):
    size = (func.octet_length(SysDiagram.content) if db.bind.dialect.name == "postgresql"
            else func.length(cast(SysDiagram.content, LargeBinary)))
    stmt = select(func.count(), func.coalesce(func.sum(size), 0)).where(SysDiagram.user_id == user_id)
    if replacing is not None:
        stmt = stmt.where(SysDiagram.id != replacing)
    count, used = (await db.execute(stmt)).one()
    if count >= settings.DIAGRAM_TOTAL_QUOTA or used + len(content.encode("utf-8")) > settings.DIAGRAM_BYTE_QUOTA:
        raise HTTPException(409, "流程图总存储已达上限（含回收站），请永久删除不需要的回收站记录")


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
    data: DiagramIn,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_user(db, user.id)
    # 每用户配额（TD-64）：不设的话任何人都能无限建图把库刷满。
    # 只数存活行，所以删掉一张就腾出一个名额。
    if await _live_count(db, user) >= settings.DIAGRAM_QUOTA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"流程图数量已达上限（{settings.DIAGRAM_QUOTA} 张），请先删除一些再新建",
        )
    await _storage_budget(db, user.id, data.content)
    diagram = SysDiagram(user_id=user.id, name=data.name, content=data.content)
    db.add(diagram)
    await db.commit()
    await db.refresh(diagram)
    response.headers["ETag"] = _etag(diagram)  # 新建完就把版本给客户端，省一次 GET
    return diagram


@router.get("/{diagram_id}", response_model=DiagramOut)
async def get_diagram(
    diagram_id: int,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    diagram = await _owned(db, user, diagram_id)
    response.headers["ETag"] = _etag(diagram)
    return diagram


@router.put("/{diagram_id}", response_model=DiagramOut)
async def update_diagram(
    diagram_id: int,
    data: DiagramIn,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存（乐观锁，TD-65）。

    客户端必须用 `If-Match` 带上它手上那一版的版本号：
      - 缺这个头 → **428**（不放行：默认放行等于仍然会被静默覆盖）
      - 版本对不上 → **412**，一个字都不写
      - 对得上 → 写入并把 version +1

    判定用的是**原子 CAS**（`UPDATE ... WHERE version = 期望值`）+ 检查 rowcount，
    不是「读出来比一比再写」。后者在并发下两个请求会同时读到同一个版本、
    都通过检查、都写进去 —— 后写覆盖先写，锁等于没加。这与 TD-158（下载端点
    必须看 `mark_downloaded()` 的返回值）是同一个道理。
    """
    expected = _parse_if_match(if_match)
    await lock_user(db, user.id)
    await _storage_budget(db, user.id, data.content, diagram_id)
    diagram = await _owned(db, user, diagram_id)  # 不存在 / 不是自己的 / 已删除 → 404
    result = await db.execute(
        update(SysDiagram)
        .where(
            SysDiagram.id == diagram_id,
            SysDiagram.user_id == user.id,
            SysDiagram.version == expected,
            _alive(),
        )
        .values(name=data.name, content=data.content, version=expected + 1),
        execution_options={"synchronize_session": False},
    )
    # 走到这里说明存在性与归属已经确认过了，所以 rowcount == 0 只可能是版本冲突。
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            f"云端已经是第 {diagram.version} 版，你手上是第 {expected} 版；"
            "为避免覆盖别人的改动，本次保存未写入。请重新打开最新版本再改。",
        )
    await db.commit()
    await db.refresh(diagram)  # 会话是 expire_on_commit=False，不 refresh 会拿到旧值
    response.headers["ETag"] = _etag(diagram)
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
    diagram_id: int, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """从回收站恢复。

    恢复要占配额 —— 否则「建满 → 删 → 恢复」就能绕过上限。
    """
    await lock_user(db, user.id)
    diagram = await _owned(db, user, diagram_id, include_deleted=True)
    if diagram.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该流程图没有被删除")
    if await _live_count(db, user) >= settings.DIAGRAM_QUOTA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"恢复失败：存活流程图已达上限（{settings.DIAGRAM_QUOTA} 张）",
        )
    diagram.deleted_at = None
    diagram.version += 1
    await db.commit()
    await db.refresh(diagram)
    response.headers["ETag"] = _etag(diagram)
    return diagram


@router.delete("/{diagram_id}/purge", status_code=204)
async def purge_diagram(diagram_id: int, if_match: str | None = Header(None, alias="If-Match"),
                        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Explicit irreversible deletion, only for this user's trash and current version."""
    expected = _parse_if_match(if_match)
    await lock_user(db, user.id)
    row = await _owned(db, user, diagram_id, include_deleted=True)
    if row.deleted_at is None:
        raise HTTPException(409, "只能永久删除回收站中的流程图")
    result = await db.execute(delete(SysDiagram).where(
        SysDiagram.id == diagram_id, SysDiagram.user_id == user.id,
        SysDiagram.deleted_at.is_not(None), SysDiagram.version == expected))
    if result.rowcount != 1:
        raise HTTPException(412, "流程图版本已变化，请刷新回收站")
    await db.commit()
