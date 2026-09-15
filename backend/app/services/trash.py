"""删除快照与多步撤销 / 重做。

快照表用 stack 字段分成两个栈:
- undo:删除操作产生,LIFO(后删的先恢复);
- redo:撤销后的快照转入,FIFO(先撤销的先重放)。

删除实体/关系前先快照(含它挂着的证据),撤销按原 id 恢复,
重做则重新执行删除。恢复/删除逻辑只有一份,端点与 redo 共用。
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Entity, Evidence, Relation, Trash
from .graph_store import recalc_mention_counts


def _dump_entity(entity):
    return {
        "id": entity.id,
        "name": entity.name,
        "type": entity.type,
        "description": entity.description or "",
        "status": entity.status,
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
    }


def _dump_relation(relation):
    return {
        "id": relation.id,
        "source_id": relation.source_id,
        "target_id": relation.target_id,
        "relation_type": relation.relation_type,
        "description": relation.description or "",
        "status": relation.status,
        "created_at": relation.created_at.isoformat() if relation.created_at else None,
    }


def _dump_evidence(rows):
    return [
        {
            "id": row.id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "document_id": row.document_id,
            "chunk_id": row.chunk_id,
            "quote": row.quote,
        }
        for row in rows
    ]


def _parse_dt(value):
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()


# ---------------- 彻底删除(统一入口) ----------------
def hard_delete_entity(db: Session, entity_id: int) -> None:
    """删除实体,连带它的关系与证据(不写快照)。"""
    rel_ids = [
        row[0]
        for row in db.query(Relation.id)
        .filter((Relation.source_id == entity_id) | (Relation.target_id == entity_id))
        .all()
    ]
    if rel_ids:
        db.query(Evidence).filter(
            Evidence.source_type == "relation", Evidence.source_id.in_(rel_ids)
        ).delete(synchronize_session=False)
        db.query(Relation).filter(Relation.id.in_(rel_ids)).delete(synchronize_session=False)
    db.query(Evidence).filter(
        Evidence.source_type == "entity", Evidence.source_id == entity_id
    ).delete(synchronize_session=False)
    entity = db.get(Entity, entity_id)
    if entity is not None:
        db.delete(entity)
    db.commit()


def hard_delete_relation(db: Session, relation_id: int) -> None:
    """删除关系,连带它的证据(不写快照)。"""
    db.query(Evidence).filter(
        Evidence.source_type == "relation", Evidence.source_id == relation_id
    ).delete(synchronize_session=False)
    relation = db.get(Relation, relation_id)
    if relation is not None:
        db.delete(relation)
    db.commit()


# ---------------- 快照 ----------------
def _owner_clause(model, owner_id):
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _clear_redo(db: Session, owner_id=None) -> None:
    """发生新的删除时,当前用户的 redo 栈失效(与常见编辑器一致)。"""
    db.query(Trash).filter(Trash.stack == "redo").filter(
        _owner_clause(Trash, owner_id)
    ).delete(synchronize_session=False)


def snapshot_entity(db: Session, entity: Entity) -> Trash:
    """快照:实体本身 + 它参与的全部关系 + 相关证据。"""
    relations = (
        db.query(Relation)
        .filter((Relation.source_id == entity.id) | (Relation.target_id == entity.id))
        .all()
    )
    rel_ids = [r.id for r in relations]
    evidence = (
        db.query(Evidence)
        .filter(
            ((Evidence.source_type == "entity") & (Evidence.source_id == entity.id))
            | ((Evidence.source_type == "relation") & (Evidence.source_id.in_(rel_ids)))
        )
        .all()
        if rel_ids
        else db.query(Evidence)
        .filter(Evidence.source_type == "entity", Evidence.source_id == entity.id)
        .all()
    )
    payload = {
        "entity": _dump_entity(entity),
        "relations": [_dump_relation(r) for r in relations],
        "evidence": _dump_evidence(evidence),
    }
    _clear_redo(db, entity.owner_id)
    snap = Trash(
        kind="entity",
        label=entity.name,
        stack="undo",
        snapshot=json.dumps(payload, ensure_ascii=False),
        owner_id=entity.owner_id,
    )
    db.add(snap)
    db.flush()
    return snap


def snapshot_relation(db: Session, relation: Relation) -> Trash:
    """快照:关系本身 + 它的证据;label 为「源 → 目标 · 类型」。"""
    evidence = (
        db.query(Evidence)
        .filter(Evidence.source_type == "relation", Evidence.source_id == relation.id)
        .all()
    )
    names = dict(
        db.query(Entity.id, Entity.name)
        .filter(Entity.id.in_([relation.source_id, relation.target_id]))
        .all()
    )
    label = (
        f"{names.get(relation.source_id, '?')} → "
        f"{names.get(relation.target_id, '?')} · {relation.relation_type}"
    )
    payload = {
        "relation": _dump_relation(relation),
        "evidence": _dump_evidence(evidence),
    }
    _clear_redo(db, relation.owner_id)
    snap = Trash(
        kind="relation",
        label=label,
        stack="undo",
        snapshot=json.dumps(payload, ensure_ascii=False),
        owner_id=relation.owner_id,
    )
    db.add(snap)
    db.flush()
    return snap


# ---------------- 撤销 / 重做 ----------------
def restore(db: Session, snapshot_id: int, owner_id=None):
    """撤销:按快照恢复(复用原 id),快照转入 redo 栈(只能恢复自己的)。"""
    snap = db.get(Trash, snapshot_id)
    if snap is None or snap.owner_id != owner_id:
        return None

    data = json.loads(snap.snapshot)
    kind, label = snap.kind, snap.label

    # 恢复出来的数据必须挂回撤销者名下,否则会变成谁都看不到的无主数据
    if kind == "entity":
        item = data["entity"]
        db.add(
            Entity(
                id=item["id"],
                name=item["name"],
                type=item["type"],
                description=item["description"],
                status=item["status"],
                created_at=_parse_dt(item["created_at"]),
                owner_id=owner_id,
            )
        )
        restored_id = item["id"]
    else:
        item = data["relation"]
        db.add(
            Relation(
                id=item["id"],
                source_id=item["source_id"],
                target_id=item["target_id"],
                relation_type=item["relation_type"],
                description=item["description"],
                status=item["status"],
                created_at=_parse_dt(item["created_at"]),
                owner_id=owner_id,
            )
        )
        restored_id = item["id"]

    for relation in data.get("relations", []):
        db.add(
            Relation(
                id=relation["id"],
                source_id=relation["source_id"],
                target_id=relation["target_id"],
                relation_type=relation["relation_type"],
                description=relation["description"],
                status=relation["status"],
                created_at=_parse_dt(relation["created_at"]),
                owner_id=owner_id,
            )
        )
    for evidence in data.get("evidence", []):
        db.add(
            Evidence(
                id=evidence["id"],
                source_type=evidence["source_type"],
                source_id=evidence["source_id"],
                document_id=evidence["document_id"],
                chunk_id=evidence["chunk_id"],
                quote=evidence["quote"],
                owner_id=owner_id,
            )
        )

    snap.stack = "redo"
    db.commit()

    # 恢复回来的证据会改变实体热度,顺带重算一次
    if data.get("evidence"):
        recalc_mention_counts(db, owner_id=owner_id)
        db.commit()
    return {"kind": kind, "id": restored_id, "label": label}


def restore_latest(db: Session, owner_id=None):
    """撤销栈顶(LIFO):恢复最近一次删除(只恢复自己的)。"""
    snap = (
        db.query(Trash)
        .filter(Trash.stack == "undo")
        .filter(_owner_clause(Trash, owner_id))
        .order_by(Trash.created_at.desc(), Trash.id.desc())
        .first()
    )
    if snap is None:
        return None
    return restore(db, snap.id, owner_id)


def redo_latest(db: Session, owner_id=None):
    """重做:重放最近一次被撤销的删除(FIFO,先撤销的先重放;只重放自己的)。"""
    snap = (
        db.query(Trash)
        .filter(Trash.stack == "redo")
        .filter(_owner_clause(Trash, owner_id))
        .order_by(Trash.created_at.asc(), Trash.id.asc())
        .first()
    )
    if snap is None:
        return None

    data = json.loads(snap.snapshot)
    kind, label = snap.kind, snap.label

    if kind == "entity":
        target_id = data["entity"]["id"]
        hard_delete_entity(db, target_id)
    else:
        target_id = data["relation"]["id"]
        hard_delete_relation(db, target_id)

    snap.stack = "undo"
    db.commit()
    return {"kind": kind, "id": target_id, "label": label}


# ---------------- 栈查询 ----------------
def list_stacks(db: Session, owner_id=None):
    """返回双栈(只包含当前用户的记录):undo 最近删除在前,redo 按重放顺序在前。"""
    rows = (
        db.query(Trash)
        .filter(_owner_clause(Trash, owner_id))
        .order_by(Trash.created_at.asc(), Trash.id.asc())
        .all()
    )

    def dump(row):
        return {
            "id": row.id,
            "kind": row.kind,
            "label": row.label,
            "stack": row.stack,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }

    undo = [dump(r) for r in rows if r.stack == "undo"]
    redo = [dump(r) for r in rows if r.stack == "redo"]
    undo.reverse()
    return {"undo": undo, "redo": redo}


def clear_all(db: Session, owner_id=None) -> int:
    """丢弃当前用户的全部撤销 / 重做记录。"""
    count = db.query(Trash).filter(_owner_clause(Trash, owner_id)).delete()
    db.commit()
    return count
