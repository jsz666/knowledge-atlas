"""知识空间:把文档按主题 / 项目分组。

空间只挂在文档上;图谱、统计、检索与问答通过
「文档 → 证据 → 实体」的链路把范围限定在某个空间内。
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Document, Share, Space, User
from ..schemas import SpaceCreate, SpaceOut, SpaceUpdate
from ..services.spaces import entity_ids_in_space

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


def _load_own_space(db: Session, space_id: int, owner_id: int) -> Space:
    space = db.get(Space, space_id)
    if space is None or space.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="空间不存在")
    return space


def _space_out(
    db: Session, space: Space, owner_id: int, document_count: int | None = None
) -> SpaceOut:
    if document_count is None:
        document_count = (
            db.query(func.count(Document.id))
            .filter(Document.owner_id == owner_id, Document.space_id == space.id)
            .scalar()
            or 0
        )
    scope = entity_ids_in_space(db, owner_id, space.id)
    return SpaceOut(
        id=space.id,
        name=space.name,
        description=space.description or "",
        color=space.color or "#4f8ef7",
        document_count=document_count,
        entity_count=len(scope or []),
        created_at=space.created_at,
    )


@router.get("", response_model=list[SpaceOut])
def list_spaces(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """我的空间列表(含每个空间的文档数与实体数)。"""
    spaces = (
        db.query(Space).filter(Space.owner_id == user.id).order_by(Space.created_at).all()
    )
    counts = {
        row[0]: row[1]
        for row in db.query(Document.space_id, func.count(Document.id))
        .filter(Document.owner_id == user.id)
        .filter(Document.space_id.isnot(None))
        .group_by(Document.space_id)
        .all()
    }
    return [_space_out(db, space, user.id, counts.get(space.id, 0)) for space in spaces]


@router.post("", response_model=SpaceOut)
def create_space(
    payload: SpaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="空间名不能为空")
    if db.query(Space).filter(Space.owner_id == user.id, Space.name == name).one_or_none():
        raise HTTPException(status_code=409, detail="同名空间已存在")

    space = Space(
        name=name,
        description=payload.description,
        color=payload.color,
        owner_id=user.id,
    )
    db.add(space)
    db.commit()
    db.refresh(space)
    return _space_out(db, space, user.id, 0)


@router.patch("/{space_id}", response_model=SpaceOut)
def update_space(
    space_id: int,
    payload: SpaceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    space = _load_own_space(db, space_id, user.id)

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="空间名不能为空")
        duplicate = (
            db.query(Space)
            .filter(Space.owner_id == user.id, Space.name == name, Space.id != space_id)
            .one_or_none()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="同名空间已存在")
        space.name = name
    if payload.description is not None:
        space.description = payload.description
    if payload.color is not None:
        space.color = payload.color

    db.commit()
    db.refresh(space)
    return _space_out(db, space, user.id)


@router.delete("/{space_id}")
def delete_space(
    space_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除空间:文档不会被删除,只是回到「未归档」。"""
    space = _load_own_space(db, space_id, user.id)
    # 旧库外键可能没有 SET NULL,先显式解绑再删,避免外键约束失败
    unfiled = (
        db.query(Document)
        .filter(Document.space_id == space_id)
        .update({"space_id": None}, synchronize_session=False)
    )
    db.delete(space)
    db.commit()
    return {"deleted": space_id, "unfiled_documents": unfiled}


# ---------------- 只读分享 ----------------
def _active_share(db: Session, space_id: int):
    return (
        db.query(Share)
        .filter(Share.kind == "space", Share.target_id == space_id, Share.revoked.is_(False))
        .one_or_none()
    )


@router.get("/{space_id}/share")
def share_status(
    space_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前空间是否处于分享中。"""
    _load_own_space(db, space_id, user.id)
    share = _active_share(db, space_id)
    return {
        "shared": bool(share),
        "token": share.token if share else None,
        "view_count": share.view_count if share else 0,
    }


@router.post("/{space_id}/share")
def create_share(
    space_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """生成只读分享令牌;已分享过则复用原令牌,旧链接继续有效。"""
    _load_own_space(db, space_id, user.id)
    existing = _active_share(db, space_id)
    if existing:
        return {"token": existing.token, "space_id": space_id, "created": False}

    share = Share(
        kind="space",
        target_id=space_id,
        owner_id=user.id,
        token=secrets.token_urlsafe(16),
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return {"token": share.token, "space_id": space_id, "created": True}


@router.delete("/{space_id}/share")
def revoke_share(
    space_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销分享:链接立即失效,空间与文档本身不受影响。"""
    _load_own_space(db, space_id, user.id)
    count = (
        db.query(Share)
        .filter(Share.kind == "space", Share.target_id == space_id, Share.revoked.is_(False))
        .update({"revoked": True}, synchronize_session=False)
    )
    db.commit()
    return {"revoked": count, "space_id": space_id}
