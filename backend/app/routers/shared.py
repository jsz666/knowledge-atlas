"""只读分享:拿到令牌的人无需登录,也能查看某个知识空间。

只返回图谱与文档标题 —— 所有写接口依然要求登录,所以分享天然是只读的;
星标属于个人隐私,对外一律不暴露。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, Entity, Relation, Share, Space, User
from ..services.spaces import entity_ids_in_space
from .graph import _compute_stats, _degree_map, _doc_count_map, _scoped, _scoped_relations

router = APIRouter(prefix="/api/shared", tags=["shared"])


@router.get("/{token}")
def read_shared(token: str, db: Session = Depends(get_db)):
    """按令牌读取被分享的空间(无需登录)。"""
    share = (
        db.query(Share).filter(Share.token == token, Share.revoked.is_(False)).one_or_none()
    )
    if share is None or share.kind != "space":
        raise HTTPException(status_code=404, detail="分享链接无效或已被撤销")

    space = db.get(Space, share.target_id)
    if space is None:
        raise HTTPException(status_code=404, detail="分享内容已不存在")

    owner = db.get(User, share.owner_id)
    owner_id = share.owner_id
    scope = entity_ids_in_space(db, owner_id, space.id)

    degrees = _degree_map(db, owner_id, scope)
    doc_counts = _doc_count_map(db, owner_id)
    entities = _scoped(
        db.query(Entity)
        .filter(Entity.owner_id == owner_id)
        .filter(Entity.status != "rejected"),
        Entity.id,
        scope,
    ).all()
    relations = _scoped_relations(
        db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status != "rejected"),
        scope,
    ).all()
    documents = (
        db.query(Document)
        .filter(Document.owner_id == owner_id, Document.space_id == space.id)
        .order_by(Document.created_at.desc())
        .all()
    )

    share.view_count = (share.view_count or 0) + 1
    db.commit()

    return {
        "kind": "space",
        "title": space.name,
        "description": space.description or "",
        "owner": owner.username if owner else "",
        "stats": _compute_stats(db, owner_id, space.id),
        "graph": {
            "nodes": [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.type,
                    "status": e.status,
                    "degree": degrees.get(e.id, 0),
                    "doc_count": doc_counts.get(e.id, 0),
                    "starred": False,
                }
                for e in entities
            ],
            "edges": [
                {
                    "id": r.id,
                    "source": r.source_id,
                    "target": r.target_id,
                    "relation_type": r.relation_type,
                    "description": r.description or "",
                    "status": r.status,
                }
                for r in relations
            ],
        },
        "documents": [
            {"id": d.id, "title": d.title, "file_type": d.file_type, "status": d.status}
            for d in documents
        ],
    }
