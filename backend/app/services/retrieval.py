"""证据检索与来源追踪。

程序负责:概念锚定到实体、按实体取回原文片段、关键词搜索。
模型只在这些结果之上做"组织语言",不做检索本身。
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Chunk, Document, Entity, EntityAlias, Evidence, Relation
from ..schemas import EvidenceOut, GraphEdge, GraphNode, SearchResult
from .spaces import entity_ids_in_space

logger = logging.getLogger(__name__)


def _owner_clause(model, owner_id: Optional[int]):
    """归属过滤条件:登录用户看自己的,匿名只看无主的公共数据。"""
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _to_evidence_out(row) -> EvidenceOut:
    evidence, document_title, locator = row
    return EvidenceOut(
        id=evidence.id,
        source_type=evidence.source_type,
        source_id=evidence.source_id,
        document_id=evidence.document_id,
        document_title=document_title or "",
        chunk_id=evidence.chunk_id,
        locator=locator or "",
        quote=evidence.quote,
    )


def evidence_rows_for(
    db: Session,
    source_type: str,
    source_ids: List[int],
    limit_per_source: int = 3,
    owner_id: Optional[int] = None,
    space_id: Optional[int] = None,
):
    """按实体/关系批量取原文证据,每个来源最多 limit_per_source 条。

    space_id 非空时,只取该空间文档里的原文。
    """
    if not source_ids:
        return []
    query = (
        db.query(Evidence, Document.title, Chunk.locator)
        .join(Document, Evidence.document_id == Document.id)
        .join(Chunk, Evidence.chunk_id == Chunk.id)
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Evidence.source_type == source_type)
        .filter(Evidence.source_id.in_(source_ids))
    )
    if space_id:
        query = query.filter(Document.space_id == space_id)
    rows = query.all()
    bucket: Dict[int, list] = {}
    for row in rows:
        bucket.setdefault(row[0].source_id, []).append(row)
    result = []
    for source_id in source_ids:
        result.extend(bucket.get(source_id, [])[:limit_per_source])
    return result


def find_anchor_entities(
    db: Session,
    question: str,
    concepts: List[str],
    limit: int = 6,
    owner_id: Optional[int] = None,
    space_id: Optional[int] = None,
) -> List[Entity]:
    """把问题与概念锚定到图谱中的实体(只锚定当前用户的图谱,可限定空间)。

    优先用模型给出的概念做匹配;模型不可用或没匹配上时,
    直接用实体名在问题中做包含匹配,保证链路不断。
    """
    scope = entity_ids_in_space(db, owner_id, space_id)
    if scope is not None and not scope:
        return []                                  # 该空间里还没有实体

    query = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "rejected")
    )
    if scope is not None:
        query = query.filter(Entity.id.in_(scope))
    entities = query.all()
    lowered_question = (question or "").lower()
    scored = []

    for entity in entities:
        name = (entity.name or "").lower()
        if not name:
            continue
        score = 0
        if name in lowered_question:
            score += 3
        for concept in concepts:
            concept_l = (concept or "").lower()
            if not concept_l:
                continue
            if concept_l == name:
                score += 4
            elif concept_l in name or name in concept_l:
                score += 2
        if score:
            scored.append((score, entity))

    scored.sort(key=lambda item: (-item[0], len(item[1].name)))
    return [entity for _, entity in scored[:limit]]


def keyword_search(
    db: Session,
    keyword: str,
    limit: int = 30,
    owner_id: Optional[int] = None,
    space_id: Optional[int] = None,
) -> SearchResult:
    """按实体名 / 关系类型 / 原文内容搜索(只搜当前用户的数据,可限定空间)。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return SearchResult()

    scope = entity_ids_in_space(db, owner_id, space_id)
    if scope is not None and not scope:
        return SearchResult()

    pattern = f"%{keyword}%"
    entity_query = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "rejected")
        .filter(Entity.name.like(pattern))
    )
    if scope is not None:
        entity_query = entity_query.filter(Entity.id.in_(scope))
    entities = entity_query.limit(limit).all()
    entity_ids = [e.id for e in entities]

    # 命中别名:消歧合并后被并掉的旧名字,仍然要能搜到合并后的实体
    alias_query = (
        db.query(EntityAlias.entity_id)
        .join(Entity, Entity.id == EntityAlias.entity_id)
        .filter(_owner_clause(Entity, owner_id))
        .filter(EntityAlias.alias.like(pattern))
    )
    if scope is not None:
        alias_query = alias_query.filter(Entity.id.in_(scope))
    alias_rows = alias_query.limit(limit).all()
    entity_ids.extend(row[0] for row in alias_rows)

    relation_query = (
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status != "rejected")
        .filter(Relation.relation_type.like(pattern))
    )
    if scope is not None:
        relation_query = relation_query.filter(
            Relation.source_id.in_(scope), Relation.target_id.in_(scope)
        )
    relations = relation_query.limit(limit).all()
    for rel in list(relations):
        entity_ids.extend([rel.source_id, rel.target_id])
    entity_ids = sorted(set(entity_ids))

    node_rows = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.id.in_(entity_ids))
        .all()
        if entity_ids
        else []
    )
    nodes = [
        GraphNode(id=e.id, name=e.name, type=e.type, status=e.status)
        for e in _sort_nodes(node_rows, entities)
    ]

    edges = [
        GraphEdge(
            id=r.id,
            source=r.source_id,
            target=r.target_id,
            relation_type=r.relation_type,
            status=r.status,
        )
        for r in relations
    ]

    chunk_query = (
        db.query(Evidence, Document.title, Chunk.locator)
        .join(Document, Evidence.document_id == Document.id)
        .join(Chunk, Evidence.chunk_id == Chunk.id)
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Chunk.content.like(pattern))
    )
    if space_id:
        chunk_query = chunk_query.filter(Document.space_id == space_id)
    chunk_rows = chunk_query.limit(limit * 3).all()
    # 同一段落会同时挂在多个实体上,按 chunk 去重,避免重复展示
    seen_chunks = set()
    evidence = []
    for row in chunk_rows:
        chunk_id = row[0].chunk_id
        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        evidence.append(_to_evidence_out(row))
        if len(evidence) >= limit:
            break

    return SearchResult(nodes=nodes, edges=edges, evidence=evidence)


def _sort_nodes(node_rows: List[Entity], preferred: List[Entity]) -> List[Entity]:
    """让搜索命中的实体排在前面。"""
    preferred_ids = {e.id for e in preferred}
    return sorted(node_rows, key=lambda e: (e.id not in preferred_ids, e.name))
