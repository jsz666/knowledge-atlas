"""文档入库流水线:解析 → 分块 → AI 抽取 → 落库(带来源证据)。"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from .. import config
from ..models import Chunk, Document, Entity, EntityAlias, Evidence, Relation
from . import progress
from .extractor import DraftEntity, DraftRelation, ExtractionResult, extract_from_chunk
from .graph_store import recalc_mention_counts
from .llm import get_llm
from .parser import parse_file
from .settings import extraction_prefs

logger = logging.getLogger(__name__)


def _sentence_containing(text: str, term: str, max_len: int = 300) -> str:
    """截取包含该术语的句子作为证据原文,比整段截取更精确。"""
    for sentence in re.split(r"(?<=[。;.!?！？\n])", text):
        if term and term in sentence:
            return sentence.strip()[:max_len]
    return text.strip()[:max_len]


def _evidence_quote(chunk_text: str, draft_evidence: str, fallback_term: str) -> str:
    """优先采用模型给出的原文证据,但必须能在片段里对上,防止模型编造出处。"""
    evidence = (draft_evidence or "").strip()
    if evidence:
        probe = evidence.strip("\"'“”‘’「」《》 ")
        if probe and probe in chunk_text:
            return evidence[:300]
        # 模型可能压缩或改动了空白,做一次宽松匹配
        if probe and re.sub(r"\s+", "", probe) in re.sub(r"\s+", "", chunk_text):
            return evidence[:300]
    return _sentence_containing(chunk_text, fallback_term)


def _add_alias(db: Session, entity: Entity, alias: str) -> None:
    """记录模型给出的别名(英文名/缩写/全称),让搜索与消歧能命中同一个实体。"""
    alias = (alias or "").strip()
    if not alias or alias == entity.name:
        return
    exists = (
        db.query(EntityAlias)
        .filter(EntityAlias.entity_id == entity.id, EntityAlias.alias == alias)
        .one_or_none()
    )
    if exists is None:
        db.add(EntityAlias(entity_id=entity.id, alias=alias))


def _get_or_create_entity(db: Session, draft: DraftEntity, owner_id: Optional[int]) -> Entity:
    entity = (
        db.query(Entity)
        .filter(Entity.name == draft.name, Entity.type == draft.type)
        .filter(Entity.owner_id == owner_id)
        .one_or_none()
    )
    if entity is None:
        entity = Entity(
            name=draft.name,
            type=draft.type,
            description=draft.description,
            status="pending",
            owner_id=owner_id,
        )
        db.add(entity)
        db.flush()
    elif not entity.description and draft.description:
        entity.description = draft.description
    for alias in getattr(draft, "aliases", []) or []:
        _add_alias(db, entity, alias)
    return entity


def _owner_clause(model, owner_id: Optional[int]):
    """归属过滤:登录用户的数据归自己,None 表示无主的历史数据。"""
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _add_evidence(
    db: Session,
    source_type: str,
    source_id: int,
    document_id: int,
    chunk: Chunk,
    quote: str,
    owner_id: Optional[int] = None,
) -> None:
    exists = (
        db.query(Evidence)
        .filter(
            Evidence.source_type == source_type,
            Evidence.source_id == source_id,
            Evidence.chunk_id == chunk.id,
        )
        .first()
    )
    if exists:
        return
    db.add(
        Evidence(
            source_type=source_type,
            source_id=source_id,
            document_id=document_id,
            chunk_id=chunk.id,
            quote=quote[:500],
            owner_id=owner_id,
        )
    )


def _persist_extraction(
    db: Session,
    document: Document,
    chunk: Chunk,
    result: ExtractionResult,
) -> None:
    owner_id = document.owner_id
    created: dict = {}
    for draft in result.entities:
        entity = _get_or_create_entity(db, draft, owner_id)
        created[draft.name] = entity
        _add_evidence(
            db,
            source_type="entity",
            source_id=entity.id,
            document_id=document.id,
            chunk=chunk,
            quote=_evidence_quote(chunk.content, getattr(draft, "evidence", ""), draft.name),
            owner_id=owner_id,
        )

    for draft in result.relations:
        source = created.get(draft.source)
        target = created.get(draft.target)
        if source is None or target is None or source.id == target.id:
            continue
        relation = (
            db.query(Relation)
            .filter(_owner_clause(Relation, owner_id))
            .filter(
                Relation.source_id == source.id,
                Relation.target_id == target.id,
                Relation.relation_type == draft.relation_type,
            )
            .one_or_none()
        )
        if relation is None:
            relation = Relation(
                source_id=source.id,
                target_id=target.id,
                relation_type=draft.relation_type,
                description=draft.description,
                status="confirmed" if result.source == "model" else "pending",
                owner_id=owner_id,
            )
            db.add(relation)
            db.flush()
        _add_evidence(
            db,
            source_type="relation",
            source_id=relation.id,
            document_id=document.id,
            chunk=chunk,
            quote=_evidence_quote(chunk.content, getattr(draft, "evidence", ""), source.name),
            owner_id=owner_id,
        )


def _extract_chunks(
    chunks: List[Chunk],
    llm,
    title: str,
    prefs: dict,
    on_done: Optional[Callable[[], None]] = None,
):
    """并发抽取文本块,返回 (块序号 → 结果, 致命错误原因)。

    抽取是网络 I/O 密集:串行逐块会把总耗时拉成「块数 × 单块延迟」,40 块就是
    十几分钟。这里用线程池并发调用模型,总耗时接近「单块延迟 × 块数 / 并发数」。

    每块的结果彼此独立、提示词完全一致,所以并发不影响单块抽取质量。
    落库不在这里做 —— 交给主线程串行处理,Session 不能跨线程使用。
    on_done 在每块结束时回调一次(含失败块),前端据此显示实时进度。
    """

    def run(index: int, chunk: Chunk):
        try:
            result = extract_from_chunk(
                chunk.content, chunk.locator, llm, title=title, **prefs
            )
        except Exception as exc:
            logger.warning("第 %s 块抽取失败,已跳过:%s", chunk.chunk_index, exc)
            result = None
        if on_done is not None:
            # 失败的块也算「跑完了」,否则进度条会永远停在那里
            on_done()
        # 失败状态按线程隔离,必须在本线程读取,才是这一块自己的状态
        return index, result, getattr(llm, "fatal_error", False), llm.last_error

    workers = max(1, min(config.EXTRACT_CONCURRENCY, len(chunks)))
    collected: List[tuple] = []

    if workers == 1 or not getattr(llm, "available", False):
        # 离线兜底或显式设为串行时并发没有收益,省去线程开销
        collected = [run(index, chunk) for index, chunk in enumerate(chunks)]
    else:
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [
                pool.submit(run, index, chunk) for index, chunk in enumerate(chunks)
            ]
            for future in as_completed(futures):
                outcome = future.result()
                collected.append(outcome)
                if outcome[2]:
                    # 致命错误(凭证/地址):剩下的调用没有意义,立刻停手
                    for pending in futures:
                        pending.cancel()
                    break
        finally:
            # cancel_futures 才能丢弃尚未开始的任务,否则退出时会等完整批
            pool.shutdown(wait=False, cancel_futures=True)

    results: dict = {}
    fatal = ""
    for index, result, is_fatal, error in collected:
        results[index] = result
        if is_fatal and not fatal:
            fatal = error
    return results, fatal


def _clear_extraction(db: Session, document: Document, drop_chunks: bool) -> None:
    """清掉这篇文档此前的抽取产物(drop_chunks 时连文本块一起删)。

    只删证据、不删「用户已确认」的实体:prune_orphans 会跳过 confirmed,
    所以重抽不会把人工确认过的成果一起抹掉。
    """
    db.query(Evidence).filter(Evidence.document_id == document.id).delete()
    if drop_chunks:
        db.query(Chunk).filter(Chunk.document_id == document.id).delete()
    db.flush()
    prune_orphans(db, document.owner_id, commit=False)


def parse_document(db: Session, document: Document) -> int:
    """解析文件并切块,把文档推进到「已分块」,返回块数。

    这一步不调用模型,所以很快。解析单独提交,让文档立刻出现在列表里,
    前端先拿到「共 N 块 / 预计耗时多久」,用户再决定用完整还是快速抽取。
    解析失败时保留原有块与结果,不会把上一次的成果删成半成品。
    """
    try:
        parsed_chunks = parse_file(Path(document.storage_path), document.file_type)
    except Exception as exc:
        logger.exception("文档解析失败:%s", document.filename)
        db.rollback()
        document.status = "failed"
        document.error = f"解析失败:{exc}"
        db.commit()
        return 0

    # 重新解析时先清掉旧块与旧证据,否则块号会重复、新旧证据会混在一起
    _clear_extraction(db, document, drop_chunks=True)

    for index, parsed in enumerate(parsed_chunks):
        db.add(
            Chunk(
                document_id=document.id,
                chunk_index=index,
                locator=parsed.locator,
                content=parsed.content,
            )
        )

    document.status = "parsed"
    document.error = ""
    db.commit()
    return len(parsed_chunks)


def extract_document(db: Session, document: Document, fast: bool = False) -> int:
    """对已分块的文档跑模型抽取,返回实际参与抽取的块数。

    fast=True 时只抽前 config.EXTRACT_FAST_MAX_CHUNKS 块,用覆盖面换时间。

    模型调用刻意放在写事务之外:抽取是几十秒的网络等待,若这段时间一直占着
    SQLite 写锁,用户此刻的任何写操作(建空间、改归属、删实体)都会等满
    busy_timeout 后报 database is locked。所以先留着旧结果,等抽取成功再在
    一个短事务里「清旧 + 写新」—— 既不再长时间占锁,抽取失败时旧结果也还在。
    """
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.chunk_index)
        .all()
    )
    if not chunks:
        # 还没解析过(或上次解析失败):先补上,否则没有东西可抽
        parse_document(db, document)
        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
            .all()
        )

    selected = chunks[: config.extract_chunk_limit(fast)]
    progress.start(
        document.id, len(selected), "fast" if fast else "full", document.owner_id
    )

    try:
        llm = get_llm()
        # 用户的自定义抽取维度(关注的实体/关系类型与领域),空表示不限制
        prefs = extraction_prefs(db, document.owner_id)
        prefs.pop("title", None)  # 避免与下面显式传入的 title 冲突

        # 这里只读库、不发写语句,事务不持有写锁,别的请求照常可写
        results, fatal = _extract_chunks(
            selected,
            llm,
            document.title,
            prefs,
            on_done=lambda: progress.advance(document.id),
        )

        # 凭证或地址错误重试无意义:立刻抛错,既不浪费几十分钟,
        # 也避免把一堆规则抽取的垃圾写进图谱;此时还没动过旧结果
        if fatal:
            raise RuntimeError(f"模型不可用:{fatal}")

        # 抽取已经成功,才真正动库:清旧 + 写新放在一个短事务里,要么全成要么全不动
        _clear_extraction(db, document, drop_chunks=False)

        degrade_reasons: set = set()
        # 抽取已并发完成,这里回到主线程按原块序串行落库(Session 非线程安全)
        for index in sorted(results):
            result = results[index]
            if result is None:
                continue
            _persist_extraction(db, document, selected[index], result)
            if result.reason:
                degrade_reasons.add(result.reason)

        document.status = "extracted"
        # 模型不可用时把降级原因写进文档,前端可据此提示「这批结果来自规则抽取」
        document.error = " | ".join(sorted(degrade_reasons)) if degrade_reasons else ""
        if degrade_reasons:
            logger.warning("文档《%s》抽取降级:%s", document.filename, document.error)
        db.commit()
    except Exception as exc:
        # 必须先 rollback:flush 失败后 Session 会进入「待回滚」状态,
        # 此时读 document.filename 这类已过期属性会触发惰性加载并抛
        # PendingRollbackError,把真正的错误盖掉,导致失败原因记录不下来
        db.rollback()
        logger.exception("文档抽取失败:%s", document.filename)
        document.status = "failed"
        document.error = str(exc)
        db.commit()
        progress.finish(document.id, str(exc))
        return 0

    # 证据数量变了,重算实体热度(图谱按它渲染节点大小)
    recalc_mention_counts(db, owner_id=document.owner_id)
    db.commit()
    progress.finish(document.id)
    return len(selected)


def prune_orphans(
    db: Session, owner_id: Optional[int] = None, commit: bool = True
) -> None:
    """清理没有任何证据支撑的实体与关系(只处理指定归属的数据)。"""
    linked_entity_ids = {
        row[0]
        for row in db.query(Evidence.source_id)
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Evidence.source_type == "entity")
        .all()
    }
    orphan_entities = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "confirmed")
    )
    if linked_entity_ids:
        orphan_entities = orphan_entities.filter(~Entity.id.in_(linked_entity_ids))
    orphan_ids = [row[0] for row in orphan_entities.with_entities(Entity.id).all()]
    if orphan_ids:
        # 旧库外键可能没有 ON DELETE CASCADE,先显式清掉子记录,否则删实体会触发外键约束失败
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).filter(
            (Relation.source_id.in_(orphan_ids)) | (Relation.target_id.in_(orphan_ids))
        ).delete(synchronize_session=False)
        db.query(Evidence).filter(_owner_clause(Evidence, owner_id)).filter(
            Evidence.source_type == "entity", Evidence.source_id.in_(orphan_ids)
        ).delete(synchronize_session=False)
        db.query(EntityAlias).filter(EntityAlias.entity_id.in_(orphan_ids)).delete(
            synchronize_session=False
        )
        db.query(Entity).filter(Entity.id.in_(orphan_ids)).delete(synchronize_session=False)

    alive_entity_ids = {
        row[0]
        for row in db.query(Entity.id).filter(_owner_clause(Entity, owner_id)).all()
    }
    if alive_entity_ids:
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).filter(
            ~Relation.source_id.in_(alive_entity_ids)
        ).delete(synchronize_session=False)
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).filter(
            ~Relation.target_id.in_(alive_entity_ids)
        ).delete(synchronize_session=False)
    else:
        db.query(Relation).filter(_owner_clause(Relation, owner_id)).delete()

    alive_relation_ids = {
        row[0]
        for row in db.query(Relation.id).filter(_owner_clause(Relation, owner_id)).all()
    }
    stale_evidence = (
        db.query(Evidence)
        .filter(_owner_clause(Evidence, owner_id))
        .filter(Evidence.source_type == "relation")
    )
    if alive_relation_ids:
        stale_evidence = stale_evidence.filter(~Evidence.source_id.in_(alive_relation_ids))
    stale_evidence.delete(synchronize_session=False)
    if commit:
        db.commit()
