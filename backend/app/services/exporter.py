"""导出:把知识星图导出为 JSON 快照 / CSV 清单 / Markdown 关系清单。

纯数据搬运,不调用模型。三种格式用途:
- JSON:完整备份(文档 / 实体 / 关系 / 证据),便于再导入或二次处理;
- CSV:表格清单,便于 Excel 统计;
- Markdown:可读的关系清单,便于贴进文档或汇报材料。
"""

import csv
import io
import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Chunk, Document, Entity, Evidence, Relation


def _one_line(text) -> str:
    """CSV / Markdown 里把换行压平,避免破坏格式。"""
    return (text or "").replace("\r", " ").replace("\n", " ").strip()


def _owner_clause(model, owner_id):
    """归属过滤:None 表示无主的公共数据。"""
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def build_json(db: Session, owner_id=None) -> str:
    """完整快照:文档 + 实体 + 关系 + 证据(只导出指定用户的数据)。"""
    documents = (
        db.query(Document).filter(_owner_clause(Document, owner_id)).order_by(Document.id).all()
    )
    entities = (
        db.query(Entity).filter(_owner_clause(Entity, owner_id)).order_by(Entity.id).all()
    )
    relations = (
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).order_by(Relation.id).all()
    )
    names = {e.id: e.name for e in entities}
    doc_ids = [d.id for d in documents]
    chunk_map = {
        c.id: c
        for c in (db.query(Chunk).filter(Chunk.document_id.in_(doc_ids)).all() if doc_ids else [])
    }
    doc_map = {d.id: d for d in documents}

    evidence_rows = []
    for ev in (
        db.query(Evidence).filter(_owner_clause(Evidence, owner_id)).order_by(Evidence.id).all()
    ):
        chunk = chunk_map.get(ev.chunk_id)
        doc = doc_map.get(ev.document_id)
        evidence_rows.append(
            {
                "id": ev.id,
                "source_type": ev.source_type,
                "source_id": ev.source_id,
                "document_id": ev.document_id,
                "document_title": doc.title if doc else "",
                "locator": chunk.locator if chunk else "",
                "quote": ev.quote or "",
            }
        )

    payload = {
        "exported_at": datetime.utcnow().isoformat(),
        "stats": {
            "documents": len(documents),
            "entities": len(entities),
            "relations": len(relations),
            "evidence": len(evidence_rows),
        },
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "filename": d.filename,
                "file_type": d.file_type,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else "",
            }
            for d in documents
        ],
        "entities": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "description": e.description or "",
                "status": e.status,
            }
            for e in entities
        ],
        "relations": [
            {
                "id": r.id,
                "source_id": r.source_id,
                "source": names.get(r.source_id, ""),
                "target_id": r.target_id,
                "target": names.get(r.target_id, ""),
                "relation_type": r.relation_type,
                "description": r.description or "",
                "status": r.status,
            }
            for r in relations
        ],
        "chunks": [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "locator": chunk.locator or "",
                "content": chunk.content,
            }
            for chunk in sorted(chunk_map.values(), key=lambda c: (c.document_id, c.chunk_index))
        ],
        "evidence": evidence_rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_csv(db: Session, kind: str = "entities", owner_id=None) -> str:
    """实体或关系的表格清单(只导出指定用户的数据)。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if kind == "relations":
        writer.writerow(["id", "source", "target", "relation_type", "description", "status"])
        names = {
            e.id: e.name
            for e in db.query(Entity).filter(_owner_clause(Entity, owner_id)).all()
        }
        for r in (
            db.query(Relation)
            .filter(_owner_clause(Relation, owner_id))
            .order_by(Relation.id)
            .all()
        ):
            writer.writerow(
                [
                    r.id,
                    names.get(r.source_id, r.source_id),
                    names.get(r.target_id, r.target_id),
                    r.relation_type,
                    _one_line(r.description),
                    r.status,
                ]
            )
    else:
        writer.writerow(["id", "name", "type", "description", "status"])
        query = db.query(Entity).filter(_owner_clause(Entity, owner_id))
        for e in query.order_by(Entity.id).all():
            writer.writerow([e.id, e.name, e.type, _one_line(e.description), e.status])

    return buffer.getvalue()


def build_markdown(db: Session, owner_id=None) -> str:
    """可读的关系清单:实体 / 关系(附证据)/ 文档(只导出指定用户的数据)。"""
    documents = (
        db.query(Document).filter(_owner_clause(Document, owner_id)).order_by(Document.id).all()
    )
    entities = (
        db.query(Entity).filter(_owner_clause(Entity, owner_id)).order_by(Entity.id).all()
    )
    relations = (
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).order_by(Relation.id).all()
    )
    names = {e.id: e.name for e in entities}
    doc_ids = [d.id for d in documents]
    chunk_map = {
        c.id: c
        for c in (db.query(Chunk).filter(Chunk.document_id.in_(doc_ids)).all() if doc_ids else [])
    }
    doc_map = {d.id: d for d in documents}

    evidence_by_source = {}
    for ev in (
        db.query(Evidence).filter(_owner_clause(Evidence, owner_id)).order_by(Evidence.id).all()
    ):
        chunk = chunk_map.get(ev.chunk_id)
        doc = doc_map.get(ev.document_id)
        evidence_by_source.setdefault((ev.source_type, ev.source_id), []).append(
            {
                "document_title": doc.title if doc else "",
                "locator": chunk.locator if chunk else "",
                "quote": ev.quote or "",
            }
        )

    lines = [
        "# 知识星图导出",
        "",
        f"导出时间:{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} (UTC)",
        "",
        f"统计:{len(documents)} 篇文档 / {len(entities)} 个实体 / "
        f"{len(relations)} 条关系 / {len(evidence_by_source)} 组来源",
        "",
        "## 实体",
        "",
    ]
    for entity in entities:
        desc = f" — {_one_line(entity.description)}" if entity.description else ""
        lines.append(f"- **{entity.name}** (`{entity.type}`, {entity.status}){desc}")

    lines += ["", "## 关系", ""]
    for relation in relations:
        source = names.get(relation.source_id, f"#{relation.source_id}")
        target = names.get(relation.target_id, f"#{relation.target_id}")
        desc = f" — {_one_line(relation.description)}" if relation.description else ""
        lines.append(
            f"- **{source}** --[{relation.relation_type}]--> **{target}** "
            f"({relation.status}){desc}"
        )
        for ev in evidence_by_source.get(("relation", relation.id), [])[:3]:
            location = f"{ev['document_title']} {ev['locator']}".strip()
            quote = _one_line(ev["quote"])[:160]
            lines.append(f"  - 证据({location}):{quote}")

    lines += ["", "## 文档", ""]
    for document in documents:
        chunk_count = len([c for c in chunk_map.values() if c.document_id == document.id])
        lines.append(
            f"- **{document.title}** ({document.file_type}, {chunk_count} 个片段, "
            f"{document.status})"
        )

    return "\n".join(lines) + "\n"
