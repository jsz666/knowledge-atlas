"""每日洞察:汇总知识库近况,交给模型组织成几句话;模型不可用时用模板兜底。

同一用户同一天只生成一次(落 insights 表),避免每次打开主页都消耗一次模型调用。
"""

import logging
from datetime import datetime, timedelta
from typing import Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Document, Entity, Insight, Relation
from .llm import get_llm

logger = logging.getLogger(__name__)

INSIGHT_SYSTEM = """你是"知识星图"助手,负责告诉用户"今天值得关注什么"。

要求:
1. 只使用【近况数据】里的信息,不要编造任何数字或事实。
2. 用 2-3 句话点出:最值得注意的变化,以及需要处理的待确认项(若有)。
3. 语气像给同事的简短提醒,不要罗列成报表。
4. 中文,120 字以内,不要 Markdown 标题。"""

INSIGHT_USER = """【近况数据】
近 7 天新增:{new_documents} 篇文档、{new_entities} 个实体、{new_relations} 条关系
当前累计:{documents} 篇文档 / {entities} 个实体 / {relations} 条关系
待确认:实体 {pending_entities} 个、关系 {pending_relations} 条
最常出现的实体:{top_entities}
跨文档实体:{cross_doc} 个"""


def _collect_stats(db: Session, owner_id: int) -> Dict:
    since = datetime.utcnow() - timedelta(days=7)
    base_doc = db.query(Document).filter(Document.owner_id == owner_id)
    base_entity = (
        db.query(Entity)
        .filter(Entity.owner_id == owner_id)
        .filter(Entity.status != "rejected")
    )
    base_relation = (
        db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status != "rejected")
    )

    top_names = [
        row[0]
        for row in base_entity.order_by(Entity.mention_count.desc(), Entity.name)
        .limit(5)
        .with_entities(Entity.name)
        .all()
    ]

    return {
        "new_documents": base_doc.filter(Document.created_at >= since).count(),
        "new_entities": base_entity.filter(Entity.created_at >= since).count(),
        "new_relations": base_relation.filter(Relation.created_at >= since).count(),
        "documents": base_doc.count(),
        "entities": base_entity.count(),
        "relations": base_relation.count(),
        "pending_entities": db.query(Entity)
        .filter(Entity.owner_id == owner_id)
        .filter(Entity.status == "pending")
        .count(),
        "pending_relations": db.query(Relation)
        .filter(Relation.owner_id == owner_id)
        .filter(Relation.status == "pending")
        .count(),
        "top_entities": "、".join(top_names) if top_names else "暂无",
        "cross_doc": _cross_doc_count(db, owner_id),
    }


def _cross_doc_count(db: Session, owner_id: int) -> int:
    from ..models import Evidence

    rows = (
        db.query(Evidence.source_id)
        .filter(Evidence.owner_id == owner_id)
        .filter(Evidence.source_type == "entity")
        .group_by(Evidence.source_id)
        .having(func.count(func.distinct(Evidence.document_id)) >= 2)
        .all()
    )
    return len(rows)


def _fallback(stats: Dict) -> str:
    lines = [
        f"近 7 天新增 {stats['new_documents']} 篇文档、"
        f"{stats['new_entities']} 个实体、{stats['new_relations']} 条关系;",
        f"当前共 {stats['documents']} 篇文档 / {stats['entities']} 个实体 / "
        f"{stats['relations']} 条关系,其中跨文档实体 {stats['cross_doc']} 个。",
    ]
    if stats["pending_entities"] or stats["pending_relations"]:
        lines.append(
            f"还有 {stats['pending_entities']} 个实体、{stats['pending_relations']} 条关系"
            "等待确认,建议优先处理。"
        )
    lines.append(f"最常出现:{stats['top_entities']}。")
    return "\n".join(lines)


def _fingerprint(stats: Dict) -> str:
    """把统计快照压成一个字符串,用来判断缓存是否还代表当前数据。"""
    return "|".join(f"{key}={stats[key]}" for key in sorted(stats))


def daily_insight(db: Session, owner_id: int) -> Insight:
    """取当天洞察;数据没变复用缓存,变了就重新生成。

    只按「用户 + 日期」缓存会让洞察一直停在旧数字上(主页写着 70 个实体、
    图谱却只有 1 个),所以这里额外比对数据指纹。
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    stats = _collect_stats(db, owner_id)
    fingerprint = _fingerprint(stats)

    existing = (
        db.query(Insight)
        .filter(Insight.owner_id == owner_id, Insight.day == today)
        .one_or_none()
    )
    if existing and (existing.fingerprint or "") == fingerprint:
        return existing

    content = ""
    available = False
    llm = get_llm()
    if llm.available:
        try:
            content = llm.chat(
                system=INSIGHT_SYSTEM,
                user=INSIGHT_USER.format(**stats),
            )
            available = True
        except Exception as exc:  # 模型异常时退回模板,不能让主页打不开
            logger.warning("洞察生成失败,使用兜底模板:%s", exc)
    if not content:
        content = _fallback(stats)

    if existing is None:
        existing = Insight(owner_id=owner_id, day=today)
        db.add(existing)
    existing.content = content
    existing.model_available = "true" if available else "false"
    existing.fingerprint = fingerprint
    existing.created_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return existing
