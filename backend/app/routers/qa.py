"""跨文档证据问答。

流程(严格分工):
1. AI 识别问题中的关键概念(可选,失败则跳过)
2. 程序:把概念锚定到图谱实体 → BFS 多跳遍历得到关系路径
3. 程序:沿路径取回原文证据片段
4. AI:基于路径与证据组织答案(证据不足时必须说明)
5. 程序:把答案 + 路径 + 证据落库,便于回看
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import QARecord, User
from ..schemas import AskRequest, AskResponse, QARecordOut
from ..services import graph_store
from ..services.extractor import extract_concepts
from ..services.llm import get_llm
from ..services.retrieval import _to_evidence_out, evidence_rows_for, find_anchor_entities
from ..services.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qa", tags=["qa"])

ANSWER_SYSTEM = """你是"知识星图"助手,负责回答需要跨多篇文档归纳的问题。

严格约束:
1. 只能使用【关系路径】和【原文证据】中的信息,禁止引入你自己的背景知识。
2. {cite}
3. 如果证据不足以支撑结论,必须明确说"现有资料不足以回答这个问题",并说明缺什么。
4. {language};{style};不要输出 Markdown 标题。
"""

ANSWER_USER = """用户问题:{question}

【关系路径】
{paths}

【原文证据】
{evidences}
"""


def _fallback_answer(question: str, concepts: List[str], paths: List[List[str]], evidences) -> str:
    """模型不可用时的兜底:给出结构化证据清单,而不是假装回答。"""
    lines = [
        "离线兜底模式:未接入模型,以下为程序检索到的证据清单,请人工归纳。",
        f"识别到的关键概念:{('、'.join(concepts) if concepts else '无(未接入概念识别)')}",
        f"命中 {len(paths)} 条关系路径、{len(evidences)} 条原文证据。",
    ]
    if paths:
        lines.append("关系路径:")
        for path in paths[:8]:
            lines.append("  - " + " → ".join(path))
    if evidences:
        lines.append("主要证据:")
        for index, item in enumerate(evidences[:6], start=1):
            location = f"{item.document_title} {item.locator}".strip()
            lines.append(f"  [{index}] {location}:{item.quote[:120]}")
    return "\n".join(lines)


@router.post("/ask", response_model=AskResponse)
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """问答会落库成历史记录,因此需要登录;检索范围只限当前用户的图谱。"""
    owner_id = user.id
    question = payload.question.strip()
    llm = get_llm()

    # 个性化偏好:回答风格 / 语言 / 是否引用证据,以及问答范围是否跟随当前空间
    settings = get_settings(db, user.id)
    space_id = payload.space_id
    if settings.qa_scope == "all":
        space_id = None

    # 1. AI:关键概念识别(失败返回空列表,不影响后续)
    concepts = extract_concepts(question, llm)

    # 2. 程序:锚定实体 + BFS 多跳遍历(可限定在某个知识空间内)
    anchors = find_anchor_entities(
        db, question, concepts, owner_id=owner_id, space_id=space_id
    )
    anchor_ids = [entity.id for entity in anchors]
    path_ids = graph_store.bfs_paths(
        db, anchor_ids, max_hops=payload.max_hops, owner_id=owner_id
    )
    names = graph_store.entity_name_map(db, owner_id=owner_id)
    paths_named = [[names.get(i, "?") for i in path] for path in path_ids]

    # 3. 程序:沿路径取原文证据(限定空间时只取该空间文档里的原文)
    entity_ids: List[int] = sorted({i for path in path_ids for i in path} | set(anchor_ids))
    evidence_rows = evidence_rows_for(
        db, "entity", entity_ids, limit_per_source=3, owner_id=owner_id, space_id=space_id
    )
    evidences = [_to_evidence_out(row) for row in evidence_rows]

    # 4. AI:按用户偏好组织答案
    answer = None
    if llm.available:
        paths_text = "\n".join(" → ".join(p) for p in paths_named[:20]) or "(无)"
        evidences_text = "\n".join(
            f"[{i}] {e.document_title} {e.locator}:{e.quote[:300]}"
            for i, e in enumerate(evidences[:12], start=1)
        ) or "(无)"
        style_hint = (
            "要点式作答,控制在 200 字以内"
            if settings.answer_style == "concise"
            else "展开说明推理过程,控制在 600 字以内"
        )
        language_hint = (
            "使用中文" if settings.answer_language == "zh" else "Answer in English"
        )
        cite_hint = (
            "先给出结论,再说明它依据哪些关系路径和证据(逐条引用原文)"
            if settings.cite_evidence
            else "先给出结论,再概括依据;不要逐条罗列原文片段"
        )
        answer = llm.chat(
            system=ANSWER_SYSTEM.format(
                cite=cite_hint, language=language_hint, style=style_hint
            ),
            user=ANSWER_USER.format(question=question, paths=paths_text, evidences=evidences_text),
        )
    if not answer:
        answer = _fallback_answer(question, concepts, paths_named, evidences)

    # 5. 程序:落库
    record = QARecord(
        question=question,
        answer=answer,
        concepts=json.dumps(concepts, ensure_ascii=False),
        paths=json.dumps(paths_named, ensure_ascii=False),
        evidences=json.dumps([e.model_dump() for e in evidences], ensure_ascii=False),
        model_available="true" if llm.available else "false",
        owner_id=owner_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return AskResponse(
        record_id=record.id,
        question=question,
        answer=answer,
        concepts=concepts,
        paths=paths_named,
        evidence=evidences,
        model_available=llm.available,
    )


@router.get("/records", response_model=list[QARecordOut])
def list_records(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """问答历史列表(只含摘要,不含路径与证据;只看自己的)。"""
    records = (
        db.query(QARecord)
        .filter(QARecord.owner_id == user.id)
        .order_by(QARecord.created_at.desc())
        .limit(limit)
        .all()
    )
    return [QARecordOut.model_validate(r) for r in records]


@router.get("/records/{record_id}")
def get_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单条问答详情:含关键概念、关系路径与证据(落库时的快照)。"""

    def _load_json(text, default):
        try:
            return json.loads(text or "")
        except Exception:
            return default

    record = db.get(QARecord, record_id)
    if record is None or record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="问答记录不存在")

    return {
        "id": record.id,
        "question": record.question,
        "answer": record.answer,
        "concepts": _load_json(record.concepts, []),
        "paths": _load_json(record.paths, []),
        "evidence": _load_json(record.evidences, []),
        "model_available": str(record.model_available).lower() == "true",
        "created_at": record.created_at,
    }
