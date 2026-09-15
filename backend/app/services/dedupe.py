"""实体消歧:找出可能是同一概念的重复实体,并支持合并。

典型重复来源:
- 大小写/空格差异:"Transformer" / "transformer"
- 中英文后缀:"Transformer" / "Transformer 模型" / "Transformer model"
- 标点差异:"RAG" / "RAG、"

合并时会把关系与证据迁移到保留实体上,并自动处理:
- 迁移后指向自身的自环关系(直接删除);
- 与已有关系重复的关系(合并,证据迁到已存在的那条);
- 重复的证据(同一片段只保留一条)。
"""

import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Entity, EntityAlias, Evidence, Relation
from .graph_store import recalc_mention_counts

# 归一化时要去掉的通用后缀
_GENERIC_SUFFIXES = ("模型", "方法", "技术", "算法", "机制", "系统", "框架", "model", "method")

_PUNCT = re.compile(r"[（）()\[\]【】<>《》,，。.、;；:：!！?？/\\|\-_~`'\"]")


def normalize_name(name: str) -> str:
    """把实体名归一化,用于判断是否为同一概念。"""
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = _PUNCT.sub("", text)
    changed = True
    while changed:
        changed = False
        for suffix in _GENERIC_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
    return text


def find_duplicates(db: Session, owner_id: Optional[int] = None) -> List[dict]:
    """按归一化名称分组,返回数量大于 1 的候选重复组(只看当前用户的数据)。"""
    groups: Dict[str, list] = {}
    query = db.query(Entity).filter(Entity.status != "rejected").filter(
        Entity.owner_id.is_(None) if owner_id is None else Entity.owner_id == owner_id
    )
    for entity in query.all():
        key = normalize_name(entity.name)
        if not key:
            continue
        groups.setdefault(key, []).append(
            {
                "id": entity.id,
                "name": entity.name,
                "type": entity.type,
                "status": entity.status,
            }
        )

    result = [
        {"key": key, "items": items}
        for key, items in groups.items()
        if len(items) > 1
    ]
    result.sort(key=lambda group: (-len(group["items"]), group["key"]))
    return result


def merge_entities(
    db: Session, keep_id: int, merge_ids: List[int], owner_id: Optional[int] = None
) -> dict:
    """把 merge_ids 合并进 keep_id,返回迁移统计。"""
    keep = db.get(Entity, keep_id)
    if keep is None:
        raise ValueError("保留的实体不存在")

    stats = {"merged_entities": 0, "relations": 0, "evidence": 0, "dropped_relations": 0}

    for old_id in merge_ids:
        if old_id == keep_id:
            continue
        old = db.get(Entity, old_id)
        if old is None:
            continue

        # 1) 关系改指向(或合并到已有关系)
        relations = (
            db.query(Relation)
            .filter((Relation.source_id == old_id) | (Relation.target_id == old_id))
            .all()
        )
        for relation in relations:
            new_source = keep_id if relation.source_id == old_id else relation.source_id
            new_target = keep_id if relation.target_id == old_id else relation.target_id

            if new_source == new_target:
                # 迁移后指向自己,直接丢弃
                db.query(Evidence).filter(
                    Evidence.source_type == "relation", Evidence.source_id == relation.id
                ).delete(synchronize_session=False)
                db.delete(relation)
                stats["dropped_relations"] += 1
                continue

            exists = (
                db.query(Relation)
                .filter(
                    Relation.source_id == new_source,
                    Relation.target_id == new_target,
                    Relation.relation_type == relation.relation_type,
                )
                .one_or_none()
            )
            if exists:
                # 重复关系:证据迁到已存在的那条,再删掉多余这条
                db.query(Evidence).filter(
                    Evidence.source_type == "relation", Evidence.source_id == relation.id
                ).update({"source_id": exists.id}, synchronize_session=False)
                db.delete(relation)
                stats["dropped_relations"] += 1
            else:
                relation.source_id = new_source
                relation.target_id = new_target
                stats["relations"] += 1
        db.flush()

        # 2) 实体证据迁移(同片段去重)
        for evidence in (
            db.query(Evidence)
            .filter(Evidence.source_type == "entity", Evidence.source_id == old_id)
            .all()
        ):
            duplicate = (
                db.query(Evidence)
                .filter(
                    Evidence.source_type == "entity",
                    Evidence.source_id == keep_id,
                    Evidence.chunk_id == evidence.chunk_id,
                )
                .first()
            )
            if duplicate:
                db.delete(evidence)
            else:
                evidence.source_id = keep_id
                stats["evidence"] += 1
        db.flush()

        # 3) 别名迁移:旧名字(以及它此前积累的别名)挂到保留实体上,
        #    这样合并之后用旧名搜索依然能命中,也能追溯「曾经叫什么」。
        for alias in db.query(EntityAlias).filter(EntityAlias.entity_id == old_id).all():
            duplicate = (
                db.query(EntityAlias)
                .filter(EntityAlias.entity_id == keep_id, EntityAlias.alias == alias.alias)
                .one_or_none()
            )
            if duplicate is None:
                alias.entity_id = keep_id
            else:
                db.delete(alias)

        if old.name and old.name != keep.name:
            exists = (
                db.query(EntityAlias)
                .filter(EntityAlias.entity_id == keep_id, EntityAlias.alias == old.name)
                .one_or_none()
            )
            if exists is None:
                db.add(EntityAlias(entity_id=keep_id, alias=old.name))
                stats["aliases"] = stats.get("aliases", 0) + 1

        # 4) 删除被合并的实体(关系 / 证据 / 别名由数据库级联清理)
        db.delete(old)
        stats["merged_entities"] += 1

    recalc_mention_counts(db, {keep_id}, owner_id=owner_id)
    db.commit()
    return stats
