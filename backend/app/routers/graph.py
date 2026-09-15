"""图数据、人工确认与图探索接口。

归属规则:
- 读取(图谱 / 统计 / 搜索 / 待确认 / 路径 / 详情)允许匿名,但只看无主的公共数据;
- 写入(新增 / 修改 / 删除 / 合并 / 撤销重做)必须登录,且只能操作自己的数据。
"""

from typing import Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import false, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_optional_user
from ..models import Document, Entity, Evidence, Relation, User
from ..schemas import (
    EntityCreate,
    EntityDetail,
    EntityOut,
    EntityUpdate,
    GraphData,
    GraphEdge,
    GraphNode,
    RelationCreate,
    RelationOut,
    RelationUpdate,
    ReviewQueue,
    SearchResult,
    UndoBatch,
)
from ..services import dedupe, graph_store, trash
from ..services.pipeline import prune_orphans
from ..services.spaces import entity_ids_in_space
from ..services.retrieval import _to_evidence_out, evidence_rows_for, keyword_search

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _owner_clause(model, owner_id: Optional[int]):
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _scoped(query, column, scope: Optional[Set[int]]):
    """按实体集合限定查询。

    scope 为 None 表示不限空间;空集合表示「这个空间里还没有实体」,
    此时要返回空结果,而不是忽略条件(否则会把全量数据当成该空间的内容)。
    """
    if scope is None:
        return query
    if not scope:
        return query.filter(false())
    return query.filter(column.in_(scope))


def _scoped_relations(query, scope: Optional[Set[int]]):
    """关系两端都要在空间内,才属于这个空间。"""
    if scope is None:
        return query
    if not scope:
        return query.filter(false())
    return query.filter(Relation.source_id.in_(scope), Relation.target_id.in_(scope))


def _degree_map(
    db: Session, owner_id: Optional[int], scope: Optional[Set[int]] = None
) -> Dict[int, int]:
    """每个实体的关系数;限定空间时只统计空间内部的关系(所见即所得)。"""
    degrees: Dict[int, int] = {}
    query = _scoped_relations(
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status != "rejected"),
        scope,
    )
    for rel in query.all():
        degrees[rel.source_id] = degrees.get(rel.source_id, 0) + 1
        degrees[rel.target_id] = degrees.get(rel.target_id, 0) + 1
    return degrees


def _doc_count_map(db: Session, owner_id: Optional[int]) -> Dict[int, int]:
    rows = (
        db.query(Evidence.source_id, func.count(func.distinct(Evidence.document_id)))
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Evidence.source_type == "entity")
        .group_by(Evidence.source_id)
        .all()
    )
    return {source_id: count for source_id, count in rows}


def _to_node(entity: Entity, degree: int = 0, doc_count: int = 0) -> GraphNode:
    return GraphNode(
        id=entity.id,
        name=entity.name,
        type=entity.type,
        status=entity.status,
        degree=degree,
        doc_count=doc_count,
        starred=bool(entity.starred),
    )


def _to_edge(relation: Relation) -> GraphEdge:
    return GraphEdge(
        id=relation.id,
        source=relation.source_id,
        target=relation.target_id,
        relation_type=relation.relation_type,
        description=relation.description or "",
        status=relation.status,
    )


def _load_own_entity(db: Session, entity_id: int, owner_id: Optional[int]) -> Entity:
    entity = db.get(Entity, entity_id)
    if entity is None or entity.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="实体不存在")
    return entity


def _load_own_relation(db: Session, relation_id: int, owner_id: Optional[int]) -> Relation:
    relation = db.get(Relation, relation_id)
    if relation is None or relation.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="关系不存在")
    return relation


@router.get("", response_model=GraphData)
def get_graph(
    space_id: int | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """整张星图:节点 + 边(只含当前用户的数据,可限定在某个知识空间内)。"""
    owner_id = user.id if user else None
    if user is None:
        space_id = None                          # 匿名数据没有空间概念
    scope = entity_ids_in_space(db, owner_id, space_id)

    degrees = _degree_map(db, owner_id, scope)
    doc_counts = _doc_count_map(db, owner_id)
    entities = _scoped(
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "rejected"),
        Entity.id,
        scope,
    ).all()
    relations = _scoped_relations(
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status != "rejected"),
        scope,
    ).all()

    return GraphData(
        nodes=[
            _to_node(e, degrees.get(e.id, 0), doc_counts.get(e.id, 0)) for e in entities
        ],
        edges=[_to_edge(r) for r in relations],
    )


def _compute_stats(
    db: Session, owner_id: Optional[int], space_id: Optional[int] = None
) -> dict:
    """图谱概览统计:统计接口与「我的主页」共用同一份口径(可限定空间)。"""
    scope = entity_ids_in_space(db, owner_id, space_id)

    documents = db.query(Document).filter(_owner_clause(Document, owner_id))
    if space_id:
        documents = documents.filter(Document.space_id == space_id)

    entities = _scoped(
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "rejected"),
        Entity.id,
        scope,
    )
    relations = _scoped_relations(
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status != "rejected"),
        scope,
    )
    pending_entities = _scoped(
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status == "pending"),
        Entity.id,
        scope,
    )
    pending_relations = _scoped_relations(
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status == "pending"),
        scope,
    )
    starred = _scoped(
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.starred.is_(True))
        .filter(Entity.status != "rejected"),
        Entity.id,
        scope,
    )
    rows = (
        db.query(Evidence.source_id, func.count(func.distinct(Evidence.document_id)))
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Evidence.source_type == "entity")
        .group_by(Evidence.source_id)
        .all()
    )
    return {
        "documents": documents.count(),
        "entities": entities.count(),
        "relations": relations.count(),
        "pending_entities": pending_entities.count(),
        "pending_relations": pending_relations.count(),
        "starred_entities": starred.count(),
        "cross_doc_entities": sum(1 for _, count in rows if count >= 2),
    }


@router.get("/stats")
def graph_stats(
    space_id: int | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """图谱概览统计(只统计当前用户的数据,可限定在某个知识空间内)。"""
    owner_id = user.id if user else None
    return _compute_stats(db, owner_id, None if user is None else space_id)


@router.get("/starred")
def list_starred(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """我关注的实体(星标),按关系数从高到低排序。"""
    owner_id = user.id if user else None
    degrees = _degree_map(db, owner_id)
    doc_counts = _doc_count_map(db, owner_id)
    entities = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.starred.is_(True))
        .filter(Entity.status != "rejected")
        .all()
    )
    entities.sort(key=lambda e: (-(degrees.get(e.id, 0)), e.name))
    return {
        "entities": [
            _to_node(e, degrees.get(e.id, 0), doc_counts.get(e.id, 0)).model_dump()
            for e in entities
        ]
    }


@router.get("/duplicates")
def list_duplicates(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """候选重复实体(归一化后同名),供人工确认后合并。"""
    return {"groups": dedupe.find_duplicates(db, user.id if user else None)}


@router.post("/merge")
def merge_entities(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """实体消歧合并:把 merge_ids 合并进 keep_id(关系与证据一并迁移)。

    注意:合并不可撤销,建议先导出快照备份。
    """
    keep_id = payload.get("keep_id")
    merge_ids = payload.get("merge_ids") or []
    if not isinstance(keep_id, int) or not isinstance(merge_ids, list) or not merge_ids:
        raise HTTPException(
            status_code=400,
            detail="请求格式应为 {keep_id: 整数, merge_ids: [实体id]}",
        )

    # 只允许合并自己的实体
    for entity_id in [keep_id, *merge_ids]:
        _load_own_entity(db, entity_id, user.id)

    try:
        stats = dedupe.merge_entities(db, keep_id, merge_ids, owner_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # 合并是批量写操作,失败要整体回滚
        db.rollback()
        raise HTTPException(status_code=500, detail=f"合并失败:{exc}") from exc
    return {"merged": stats}


@router.get("/search", response_model=SearchResult)
def search(
    q: str = "",
    space_id: int | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """按实体、关系类型或关键词搜索(只搜当前用户的数据,可限定空间)。"""
    owner_id = user.id if user else None
    return keyword_search(
        db, q, owner_id=owner_id, space_id=None if user is None else space_id
    )


@router.get("/review", response_model=ReviewQueue)
def review_queue(
    space_id: int | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """待确认草稿:AI 抽取出来但还没被人工确认的实体与关系。"""
    owner_id = user.id if user else None
    if user is None:
        space_id = None
    scope = entity_ids_in_space(db, owner_id, space_id)
    entities = _scoped(
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status == "pending"),
        Entity.id,
        scope,
    ).all()
    relations = _scoped_relations(
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status == "pending"),
        scope,
    ).all()
    return ReviewQueue(
        entities=[EntityOut.model_validate(e) for e in entities],
        relations=[RelationOut.model_validate(r) for r in relations],
    )


@router.get("/path")
def find_path(
    source_id: int,
    target_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """两点之间的最短关系路径(程序 BFS,不依赖模型)。"""
    owner_id = user.id if user else None
    _load_own_entity(db, source_id, owner_id)
    _load_own_entity(db, target_id, owner_id)

    path = graph_store.shortest_path(db, source_id, target_id, owner_id=owner_id)
    if path is None:
        return {"found": False, "path": []}
    names = graph_store.entity_name_map(db, set(path), owner_id=owner_id)
    return {"found": True, "path_ids": path, "path": [names.get(i, "?") for i in path]}


@router.get("/entities/{entity_id}", response_model=EntityDetail)
def entity_detail(
    entity_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """实体详情:邻居节点、关联边、以及支撑它的原文片段。"""
    owner_id = user.id if user else None
    entity = _load_own_entity(db, entity_id, owner_id)

    node_ids, relations = graph_store.subgraph(db, [entity_id], depth=1, owner_id=owner_id)
    node_ids.discard(entity_id)
    neighbors = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.id.in_(node_ids))
        .all()
        if node_ids
        else []
    )
    degrees = _degree_map(db, owner_id)
    doc_counts = _doc_count_map(db, owner_id)

    return EntityDetail(
        entity=EntityOut.model_validate(entity),
        neighbors=[_to_node(n, degrees.get(n.id, 0), doc_counts.get(n.id, 0)) for n in neighbors],
        edges=[_to_edge(r) for r in relations],
        evidence=[
            _to_evidence_out(row)
            for row in evidence_rows_for(
                db, "entity", [entity_id], limit_per_source=10, owner_id=owner_id
            )
        ],
    )


@router.post("/entities", response_model=EntityOut)
def create_entity(
    payload: EntityCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工新增实体(补充 AI 漏抽的概念)。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="实体名不能为空")
    exists = (
        db.query(Entity)
        .filter(Entity.name == name, Entity.type == payload.type)
        .filter(Entity.owner_id == user.id)
        .one_or_none()
    )
    if exists:
        raise HTTPException(status_code=409, detail="同名实体已存在")

    entity = Entity(
        name=name,
        type=payload.type,
        description=payload.description,
        status="confirmed",
        owner_id=user.id,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return EntityOut.model_validate(entity)


@router.patch("/entities/{entity_id}", response_model=EntityOut)
def update_entity(
    entity_id: int,
    payload: EntityUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工修正实体:改名、改类型、改描述,或确认/拒绝。"""
    entity = _load_own_entity(db, entity_id, user.id)

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="实体名不能为空")
        entity.name = name
    if payload.type is not None:
        entity.type = payload.type
    if payload.description is not None:
        entity.description = payload.description
    if payload.starred is not None:
        entity.starred = payload.starred

    # 只有实质编辑(改名/改类型/改描述)才顺带把草稿标记为已确认;
    # 单纯的星标切换不应该改变审核状态
    edited = any(
        value is not None
        for value in (payload.name, payload.type, payload.description)
    )
    if payload.status is not None:
        if payload.status not in {"pending", "confirmed", "rejected"}:
            raise HTTPException(status_code=400, detail="状态只能是 pending/confirmed/rejected")
        entity.status = payload.status
    elif edited:
        entity.status = "confirmed"

    db.commit()
    db.refresh(entity)
    return EntityOut.model_validate(entity)


@router.delete("/entities/{entity_id}")
def delete_entity(
    entity_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除实体(连带它的关系与证据);删除前存快照,支持撤销 / 重做。"""
    entity = _load_own_entity(db, entity_id, user.id)

    snapshot = trash.snapshot_entity(db, entity)
    trash.hard_delete_entity(db, entity_id)
    prune_orphans(db, user.id)
    return {"deleted": entity_id, "snapshot_id": snapshot.id}


@router.post("/relations", response_model=RelationOut)
def create_relation(
    payload: RelationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工新增关系。"""
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=400, detail="关系两端不能是同一个实体")
    for entity_id in (payload.source_id, payload.target_id):
        _load_own_entity(db, entity_id, user.id)

    exists = (
        db.query(Relation)
        .filter(Relation.owner_id == user.id)
        .filter(
            Relation.source_id == payload.source_id,
            Relation.target_id == payload.target_id,
            Relation.relation_type == payload.relation_type,
        )
        .one_or_none()
    )
    if exists:
        raise HTTPException(status_code=409, detail="相同关系已存在")

    relation = Relation(
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation_type=payload.relation_type,
        description=payload.description,
        status=payload.status,
        owner_id=user.id,
    )
    db.add(relation)
    db.commit()
    db.refresh(relation)
    return RelationOut.model_validate(relation)


@router.patch("/relations/{relation_id}", response_model=RelationOut)
def update_relation(
    relation_id: int,
    payload: RelationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工修正关系(类型 / 描述),或确认/拒绝 AI 抽出来的关系。"""
    relation = _load_own_relation(db, relation_id, user.id)

    if payload.relation_type is not None:
        relation.relation_type = payload.relation_type
    if payload.description is not None:
        relation.description = payload.description
    if payload.status is not None:
        if payload.status not in {"pending", "confirmed", "rejected"}:
            raise HTTPException(status_code=400, detail="状态只能是 pending/confirmed/rejected")
        relation.status = payload.status
    else:
        relation.status = "confirmed"

    db.commit()
    db.refresh(relation)
    return RelationOut.model_validate(relation)


@router.delete("/relations/{relation_id}")
def delete_relation(
    relation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除关系(连带它的证据);删除前存快照,支持撤销 / 重做。"""
    relation = _load_own_relation(db, relation_id, user.id)

    snapshot = trash.snapshot_relation(db, relation)
    trash.hard_delete_relation(db, relation_id)
    prune_orphans(db, user.id)
    return {"deleted": relation_id, "snapshot_id": snapshot.id}


# ---------------- 撤销 / 重做栈 ----------------
@router.post("/undo")
def undo_latest(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """撤销栈顶(LIFO):恢复最近一次删除。"""
    restored = trash.restore_latest(db, user.id)
    if restored is None:
        raise HTTPException(status_code=404, detail="没有可撤销的删除记录")
    return {"restored": restored}


@router.post("/undo/{snapshot_id}")
def undo_delete(
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销指定的某一次删除(按快照恢复,复用原 id 与证据)。"""
    restored = trash.restore(db, snapshot_id, user.id)
    if restored is None:
        raise HTTPException(status_code=404, detail="没有可撤销的删除记录")
    return {"restored": restored}


@router.post("/redo")
def redo_latest(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """重做:重放最近一次被撤销的删除(FIFO)。"""
    redone = trash.redo_latest(db, user.id)
    if redone is None:
        raise HTTPException(status_code=404, detail="没有可重做的记录")
    return {"redone": redone}


@router.post("/trash/undo-batch")
def undo_batch(
    payload: UndoBatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量撤销:一次恢复多条被删记录(勾选多条一起回退)。"""
    restored = []
    for snapshot_id in payload.ids:
        item = trash.restore(db, snapshot_id, user.id)
        if item:
            restored.append(item)
    if not restored:
        raise HTTPException(status_code=404, detail="没有可撤销的记录")
    return {"restored": restored}


@router.get("/trash")
def list_trash(db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """撤销栈与重做栈(undo 最近在前,redo 按重放顺序)。"""
    return trash.list_stacks(db, user.id if user else None)


@router.delete("/trash")
def clear_trash(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """丢弃当前用户的撤销 / 重做记录(数据将不再可恢复)。"""
    return {"cleared": trash.clear_all(db, user.id)}
