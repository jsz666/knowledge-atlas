"""图遍历算法(纯程序实现,不依赖模型)。

提供:邻接表构建、多跳 BFS 路径、两点最短路径、子图提取。
问答时由这里产出"关系路径",再交给检索层找原文、交给模型组织语言。

多用户隔离:所有函数都接受 owner_id,匿名(owner_id=None)只能看到无主的公共数据。
"""

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Entity, Evidence, Relation


def _owner_clause(model, owner_id: Optional[int]):
    """归属过滤条件:登录用户看自己的,匿名只看无主的公共数据。"""
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def recalc_mention_counts(
    db: Session,
    entity_ids: Optional[Set[int]] = None,
    owner_id: Optional[int] = None,
) -> None:
    """重算实体热度(支撑它的证据条数);不传 ids 则重算该归属下全部实体。

    注意:这里 owner_id=None 表示「不限归属」(内部维护用),不是匿名只读;
    对外读取接口请传具体 owner_id 或 None 搭配 _owner_clause 使用。
    """
    query = db.query(Evidence.source_id, func.count(Evidence.id)).filter(
        Evidence.source_type == "entity"
    )
    if entity_ids is not None:
        query = query.filter(Evidence.source_id.in_(entity_ids))

    counts = dict(query.group_by(Evidence.source_id).all())

    entities = db.query(Entity)
    if entity_ids is not None:
        entities = entities.filter(Entity.id.in_(entity_ids))
    elif owner_id is not None:
        entities = entities.filter(_owner_clause(Entity, owner_id))

    for entity in entities.all():
        entity.mention_count = counts.get(entity.id, 0)
    db.flush()


def build_adjacency(
    db: Session, include_rejected: bool = False, owner_id: Optional[int] = None
) -> Dict[int, Set[int]]:
    """无向邻接表,便于做探索式遍历。"""
    query = db.query(Relation).filter(_owner_clause(Relation, owner_id))
    if not include_rejected:
        query = query.filter(Relation.status != "rejected")

    adjacency: Dict[int, Set[int]] = {}
    for rel in query.all():
        adjacency.setdefault(rel.source_id, set()).add(rel.target_id)
        adjacency.setdefault(rel.target_id, set()).add(rel.source_id)
    return adjacency


def entity_name_map(
    db: Session, ids: Optional[Set[int]] = None, owner_id: Optional[int] = None
) -> Dict[int, str]:
    query = db.query(Entity).filter(_owner_clause(Entity, owner_id))
    if ids is not None:
        query = query.filter(Entity.id.in_(ids))
    return {e.id: e.name for e in query.all()}


def bfs_paths(
    db: Session,
    start_ids: List[int],
    max_hops: int = 2,
    max_paths: int = 40,
    owner_id: Optional[int] = None,
) -> List[List[int]]:
    """从若干锚点出发做多跳 BFS,返回实体 id 路径列表(如 [A, B, C])。

    枚举所有不超过 max_hops 跳的简单路径(用 path 判重而非全局 visited,
    否则第二跳会被第一跳访问过的节点挡住,只剩 1 跳路径)。
    路径用于解释"这两个概念是怎么连起来的",是证据组织的核心。
    """
    # 大图保护:节点很多时收敛搜索规模,避免路径枚举失控
    total = db.query(Entity.id).filter(_owner_clause(Entity, owner_id)).count()
    if total > 500:
        max_hops = min(max_hops, 2)
        max_paths = min(max_paths, 15)

    adjacency = build_adjacency(db, owner_id=owner_id)
    names = entity_name_map(db, owner_id=owner_id)
    paths: List[List[int]] = []

    for start in start_ids:
        if start not in names:
            continue
        queue = deque([(start, [start])])
        while queue:
            node, path = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue
            for nxt in sorted(adjacency.get(node, set())):
                if nxt in path:
                    continue
                new_path = path + [nxt]
                paths.append(new_path)
                if len(paths) >= max_paths:
                    return paths
                queue.append((nxt, new_path))
    return paths


def shortest_path(
    db: Session,
    source_id: int,
    target_id: int,
    max_depth: int = 5,
    owner_id: Optional[int] = None,
) -> Optional[List[int]]:
    """两点间最短路径(BFS),找不到返回 None。"""
    if source_id == target_id:
        return [source_id]

    adjacency = build_adjacency(db, owner_id=owner_id)
    queue = deque([(source_id, [source_id])])
    visited = {source_id}
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for nxt in sorted(adjacency.get(node, set())):
            if nxt == target_id:
                return path + [nxt]
            if nxt in visited:
                continue
            visited.add(nxt)
            queue.append((nxt, path + [nxt]))
    return None


def subgraph(
    db: Session,
    center_ids: List[int],
    depth: int = 1,
    owner_id: Optional[int] = None,
) -> Tuple[Set[int], List[Relation]]:
    """取中心节点 depth 跳内的子图,返回(实体 id 集合, 关系列表)。"""
    adjacency = build_adjacency(db, owner_id=owner_id)
    node_ids: Set[int] = set()
    for center in center_ids:
        visited = {center}
        queue = deque([(center, 0)])
        while queue:
            node, dist = queue.popleft()
            node_ids.add(node)
            if dist >= depth:
                continue
            for nxt in adjacency.get(node, set()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, dist + 1))
    if not node_ids:
        return set(), []
    relations = (
        db.query(Relation)
        .filter(_owner_clause(Relation, owner_id))
        .filter(Relation.status != "rejected")
        .filter(Relation.source_id.in_(node_ids), Relation.target_id.in_(node_ids))
        .all()
    )
    return node_ids, relations
