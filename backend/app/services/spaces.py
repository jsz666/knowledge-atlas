"""知识空间:把「空间」翻译成可复用的查询条件。

实体不直接挂在空间下 —— 实体与空间的关系通过「证据 → 文档」间接推导。
这里统一提供「某空间内的实体 / 文档 id 集合」,供图谱、统计、检索、问答复用,
避免各处各写一套联表逻辑。

约定:space_id 为空表示「不限空间」,返回 None(调用方据此跳过过滤)。
"""

from typing import Optional, Set

from sqlalchemy.orm import Session

from ..models import Document, Evidence


def document_ids_in_space(
    db: Session, owner_id: Optional[int], space_id: Optional[int]
) -> Optional[Set[int]]:
    """某空间内的文档 id 集合。"""
    if not space_id or owner_id is None:
        return None
    rows = (
        db.query(Document.id)
        .filter(Document.owner_id == owner_id)
        .filter(Document.space_id == space_id)
        .all()
    )
    return {row[0] for row in rows}


def entity_ids_in_space(
    db: Session, owner_id: Optional[int], space_id: Optional[int]
) -> Optional[Set[int]]:
    """某空间内的实体 id 集合:证据指向该空间的文档。

    返回空集合表示该空间里确实还没有实体,调用方应据此返回空结果,
    而不是忽略过滤条件(否则会错误地把全量图谱当成该空间的内容)。
    """
    if not space_id or owner_id is None:
        return None
    rows = (
        db.query(Evidence.source_id)
        .join(Document, Evidence.document_id == Document.id)
        .filter(Evidence.owner_id == owner_id)
        .filter(Evidence.source_type == "entity")
        .filter(Document.space_id == space_id)
        .distinct()
        .all()
    )
    return {row[0] for row in rows}
