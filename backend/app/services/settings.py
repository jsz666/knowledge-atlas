"""用户级偏好(问答风格 / 语言 / 是否引用证据 / 问答范围)。

用 KV 表存,以后加新偏好不用改表结构。
"""

import json
from typing import List

from sqlalchemy.orm import Session

from ..models import UserSetting
from ..schemas import SettingsOut, SettingsUpdate

DEFAULTS = {
    "answer_style": "concise",        # concise / detailed
    "answer_language": "zh",          # zh / en
    "cite_evidence": "true",          # 是否逐条引用原文证据
    "qa_scope": "all",                # all / current_space
    "extract_entity_types": "[]",     # 抽取时关注的实体类型,空表示不限
    "extract_relation_types": "[]",   # 抽取时关注的关系类型,空表示不限
    "extract_focus": "",              # 关注领域描述,注入抽取 prompt
}


def _load_list(value: str) -> List[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except Exception:
        return []


def get_settings(db: Session, user_id: int) -> SettingsOut:
    rows = db.query(UserSetting).filter(UserSetting.user_id == user_id).all()
    values = {row.key: row.value for row in rows}
    return SettingsOut(
        answer_style=values.get("answer_style", DEFAULTS["answer_style"]),
        answer_language=values.get("answer_language", DEFAULTS["answer_language"]),
        cite_evidence=str(
            values.get("cite_evidence", DEFAULTS["cite_evidence"])
        ).lower()
        == "true",
        qa_scope=values.get("qa_scope", DEFAULTS["qa_scope"]),
        extract_entity_types=_load_list(
            values.get("extract_entity_types", DEFAULTS["extract_entity_types"])
        ),
        extract_relation_types=_load_list(
            values.get("extract_relation_types", DEFAULTS["extract_relation_types"])
        ),
        extract_focus=values.get("extract_focus", DEFAULTS["extract_focus"]) or "",
    )


def extraction_prefs(db: Session, user_id: int) -> dict:
    """给抽取链路用的维度偏好;空列表转成 None,表示「不限制」。"""
    settings = get_settings(db, user_id)
    return {
        "entity_types": settings.extract_entity_types or None,
        "relation_types": settings.extract_relation_types or None,
        "focus": settings.extract_focus or "",
    }


def update_settings(db: Session, user_id: int, payload: SettingsUpdate) -> SettingsOut:
    """局部更新偏好;取值不合法时抛 ValueError,由路由转成 400。"""
    changes: dict = {}

    if payload.answer_style is not None:
        if payload.answer_style not in {"concise", "detailed"}:
            raise ValueError("answer_style 只能是 concise / detailed")
        changes["answer_style"] = payload.answer_style

    if payload.answer_language is not None:
        if payload.answer_language not in {"zh", "en"}:
            raise ValueError("answer_language 只能是 zh / en")
        changes["answer_language"] = payload.answer_language

    if payload.cite_evidence is not None:
        changes["cite_evidence"] = "true" if payload.cite_evidence else "false"

    if payload.qa_scope is not None:
        if payload.qa_scope not in {"all", "current_space"}:
            raise ValueError("qa_scope 只能是 all / current_space")
        changes["qa_scope"] = payload.qa_scope

    if payload.extract_entity_types is not None:
        changes["extract_entity_types"] = json.dumps(
            payload.extract_entity_types, ensure_ascii=False
        )
    if payload.extract_relation_types is not None:
        changes["extract_relation_types"] = json.dumps(
            payload.extract_relation_types, ensure_ascii=False
        )
    if payload.extract_focus is not None:
        changes["extract_focus"] = (payload.extract_focus or "")[:200]

    for key, value in changes.items():
        row = (
            db.query(UserSetting)
            .filter(UserSetting.user_id == user_id, UserSetting.key == key)
            .one_or_none()
        )
        if row is None:
            db.add(UserSetting(user_id=user_id, key=key, value=value))
        else:
            row.value = value

    db.commit()
    return get_settings(db, user_id)
