"""导入:把导出的 JSON 快照合并回当前图谱。

采用"合并"而非"覆盖"策略,重复导入同一份文件不会产生重复数据:
- 文档 按 title 匹配;
- 片段 按 文档 + chunk_index 匹配;
- 实体 按 (name, type) 匹配;
- 关系 按 (source, target, relation_type) 匹配;
- 证据 按 (来源类型, 来源 id, 片段 id) 去重。
"""

from ..models import Chunk, Document, Entity, Evidence, Relation


def import_snapshot(db, data: dict, owner_id=None) -> dict:
    """导入快照并合并到指定用户下,返回各类型的新增数量。"""
    stats = {
        "documents": 0,
        "chunks": 0,
        "entities": 0,
        "relations": 0,
        "evidence": 0,
    }

    # 1) 文档:按 title 匹配
    doc_map = {}
    for item in data.get("documents", []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        doc_query = db.query(Document).filter(Document.title == title).filter(
            Document.owner_id.is_(None) if owner_id is None
            else Document.owner_id == owner_id
        )
        document = doc_query.one_or_none()
        if document is None:
            document = Document(
                title=title,
                filename=item.get("filename") or title,
                file_type=item.get("file_type") or "txt",
                storage_path=f"imported://{title}",
                status=item.get("status") or "extracted",
                owner_id=owner_id,
            )
            db.add(document)
            db.flush()
            stats["documents"] += 1
        doc_map[item.get("id")] = document

    # 2) 片段:按 文档 + chunk_index 匹配
    chunk_map = {}
    for item in data.get("chunks", []):
        document = doc_map.get(item.get("document_id"))
        if document is None:
            continue
        index = item.get("chunk_index") or 0
        chunk = (
            db.query(Chunk)
            .filter(Chunk.document_id == document.id, Chunk.chunk_index == index)
            .one_or_none()
        )
        if chunk is None:
            chunk = Chunk(
                document_id=document.id,
                chunk_index=index,
                locator=item.get("locator") or "",
                content=item.get("content") or "",
            )
            db.add(chunk)
            db.flush()
            stats["chunks"] += 1
        chunk_map[item.get("id")] = chunk

    # 3) 实体:按 (name, type) 匹配
    entity_map = {}
    for item in data.get("entities", []):
        entity_name = (item.get("name") or "").strip()
        if not entity_name:
            continue
        etype = item.get("type") or "concept"
        entity = (
            db.query(Entity)
            .filter(Entity.name == entity_name, Entity.type == etype)
            .filter(
                Entity.owner_id.is_(None) if owner_id is None
                else Entity.owner_id == owner_id
            )
            .one_or_none()
        )
        if entity is None:
            entity = Entity(
                name=entity_name,
                type=etype,
                description=item.get("description") or "",
                status=item.get("status") or "confirmed",
                owner_id=owner_id,
            )
            db.add(entity)
            db.flush()
            stats["entities"] += 1
        entity_map[item.get("id")] = entity

    # 4) 关系:按 (source, target, relation_type) 匹配
    relation_map = {}
    for item in data.get("relations", []):
        source = entity_map.get(item.get("source_id"))
        target = entity_map.get(item.get("target_id"))
        if source is None or target is None or source.id == target.id:
            continue
        rtype = item.get("relation_type") or "related_to"
        relation = (
            db.query(Relation)
            .filter(
                Relation.source_id == source.id,
                Relation.target_id == target.id,
                Relation.relation_type == rtype,
            )
            .filter(
                Relation.owner_id.is_(None) if owner_id is None
                else Relation.owner_id == owner_id
            )
            .one_or_none()
        )
        if relation is None:
            relation = Relation(
                source_id=source.id,
                target_id=target.id,
                relation_type=rtype,
                description=item.get("description") or "",
                status=item.get("status") or "confirmed",
                owner_id=owner_id,
            )
            db.add(relation)
            db.flush()
            stats["relations"] += 1
        relation_map[item.get("id")] = relation

    # 5) 证据:按 (来源类型, 来源 id, 片段 id) 去重
    for item in data.get("evidence", []):
        document = doc_map.get(item.get("document_id"))
        if document is None:
            continue
        source_type = item.get("source_type")
        if source_type == "entity":
            source = entity_map.get(item.get("source_id"))
        else:
            source = relation_map.get(item.get("source_id"))
        if source is None:
            continue

        chunk = chunk_map.get(item.get("chunk_id"))
        if chunk is None:
            # 兼容旧快照(没有 chunks 字段):按 locator 建一个片段存摘录
            locator = item.get("locator") or ""
            chunk = (
                db.query(Chunk)
                .filter(Chunk.document_id == document.id, Chunk.locator == locator)
                .first()
            )
            if chunk is None:
                # 兜底片段固定用 index=0,先查一次,避免违反 (文档, 序号) 唯一约束
                chunk = (
                    db.query(Chunk)
                    .filter(Chunk.document_id == document.id, Chunk.chunk_index == 0)
                    .first()
                )
            if chunk is None:
                chunk = Chunk(
                    document_id=document.id,
                    chunk_index=0,
                    locator=locator,
                    content=item.get("quote") or "",
                )
                db.add(chunk)
                db.flush()
                stats["chunks"] += 1

        exists = (
            db.query(Evidence)
            .filter(
                Evidence.source_type == source_type,
                Evidence.source_id == source.id,
                Evidence.chunk_id == chunk.id,
            )
            .first()
        )
        if exists:
            continue
        db.add(
            Evidence(
                source_type=source_type,
                source_id=source.id,
                document_id=document.id,
                chunk_id=chunk.id,
                quote=item.get("quote") or "",
                owner_id=owner_id,
            )
        )
        stats["evidence"] += 1

    db.commit()
    return stats
