"""知识缺口分析:找出图谱里「该有却还没有」的部分。

三类提示:
1. 孤岛实体 —— 有原文证据支撑,却没有任何关系;
2. 单来源实体 —— 只在一篇文档里出现过,证据薄弱,值得补充资料;
3. 共现未连接 —— 两个实体常在同一篇文档出现,彼此间却没有直接关系。

只做统计分析,不调用模型,因此离线也能用。
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from ..models import Document, Entity, Evidence, Relation
from .spaces import entity_ids_in_space

logger = logging.getLogger(__name__)

ISOLATED_LIMIT = 5
WEAK_LIMIT = 5
MISSING_LINK_LIMIT = 5
PER_DOC_ENTITY_LIMIT = 8      # 每篇文档只取最热的若干实体,避免组合爆炸


def _degrees(db: Session, owner_id: int) -> Dict[int, int]:
    degrees: Dict[int, int] = defaultdict(int)
    for rel in (
        db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status != "rejected")
        .all()
    ):
        degrees[rel.source_id] += 1
        degrees[rel.target_id] += 1
    return degrees


def _doc_counts(db: Session, owner_id: int) -> Dict[int, int]:
    rows = (
        db.query(Evidence.source_id, Evidence.document_id)
        .filter(Evidence.owner_id == owner_id)
        .filter(Evidence.source_type == "entity")
        .all()
    )
    bucket: Dict[int, Set[int]] = defaultdict(set)
    for source_id, document_id in rows:
        bucket[source_id].add(document_id)
    return {source_id: len(docs) for source_id, docs in bucket.items()}


def _missing_links(
    db: Session,
    owner_id: int,
    entity_map: Dict[int, Entity],
    scope: Optional[Set[int]],
    space_id: Optional[int],
) -> List[Dict]:
    """同一篇文档里频繁共同出现、但彼此没有直接关系的实体对。"""
    rows = (
        db.query(Evidence.document_id, Evidence.source_id)
        .filter(Evidence.owner_id == owner_id)
        .filter(Evidence.source_type == "entity")
        .all()
    )
    if space_id:
        doc_ids = {
            row[0]
            for row in db.query(Document.id)
            .filter(Document.owner_id == owner_id, Document.space_id == space_id)
            .all()
        }
        rows = [row for row in rows if row[0] in doc_ids]

    entities_per_doc: Dict[int, Set[int]] = defaultdict(set)
    for document_id, source_id in rows:
        if source_id not in entity_map:
            continue
        if scope is not None and source_id not in scope:
            continue
        entities_per_doc[document_id].add(source_id)

    linked = set()
    for rel in (
        db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status != "rejected")
        .all()
    ):
        linked.add(tuple(sorted((rel.source_id, rel.target_id))))

    pairs: Dict[tuple, int] = defaultdict(int)
    for ids in entities_per_doc.values():
        # 按热度取前若干个,控制两两组合的规模
        hot = sorted(
            ids, key=lambda i: (-(entity_map[i].mention_count or 0), i)
        )[:PER_DOC_ENTITY_LIMIT]
        for i in range(len(hot)):
            for j in range(i + 1, len(hot)):
                pair = tuple(sorted((hot[i], hot[j])))
                if pair in linked:
                    continue
                pairs[pair] += 1

    result = []
    for (a, b), count in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(result) >= MISSING_LINK_LIMIT:
            break
        source, target = entity_map.get(a), entity_map.get(b)
        if not source or not target:
            continue
        result.append(
            {
                "source_id": a,
                "source": source.name,
                "target_id": b,
                "target": target.name,
                "co_occurrences": count,
            }
        )
    return result


def analyze_gaps(db: Session, owner_id: int, space_id: Optional[int] = None) -> Dict:
    scope = entity_ids_in_space(db, owner_id, space_id)
    entities = (
        db.query(Entity)
        .filter(Entity.owner_id == owner_id)
        .filter(Entity.status != "rejected")
        .all()
    )
    if scope is not None:
        entities = [e for e in entities if e.id in scope]

    pending_entities = (
        db.query(Entity)
        .filter(Entity.owner_id == owner_id)
        .filter(Entity.status == "pending")
        .all()
    )
    pending_relations = (
        db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status == "pending")
        .count()
    )
    if scope is not None:
        pending_entities = [e for e in pending_entities if e.id in scope]

    if not entities:
        return {
            "isolated": [],
            "weak_evidence": [],
            "missing_links": [],
            "pending_entities": len(pending_entities),
            "pending_relations": pending_relations,
        }

    degrees = _degrees(db, owner_id)
    doc_counts = _doc_counts(db, owner_id)
    entity_map = {e.id: e for e in entities}

    isolated = [
        {"id": e.id, "name": e.name, "type": e.type, "doc_count": doc_counts.get(e.id, 0)}
        for e in sorted(entities, key=lambda x: (-(doc_counts.get(x.id, 0)), x.name))
        if degrees.get(e.id, 0) == 0
    ][:ISOLATED_LIMIT]

    weak = [
        {"id": e.id, "name": e.name, "type": e.type, "doc_count": doc_counts.get(e.id, 0)}
        for e in sorted(entities, key=lambda x: (doc_counts.get(x.id, 0), x.name))
        if doc_counts.get(e.id, 0) <= 1 and degrees.get(e.id, 0) > 0
    ][:WEAK_LIMIT]

    return {
        "isolated": isolated,
        "weak_evidence": weak,
        "missing_links": _missing_links(db, owner_id, entity_map, scope, space_id),
        "pending_entities": len(pending_entities),
        "pending_relations": pending_relations,
    }
