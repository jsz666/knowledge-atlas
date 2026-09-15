"""个人主页:一次拿全「我的概览 / 关注 / 最近动态 / 待办」。

只有登录用户可以访问,所有数据都按 owner_id 严格隔离。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Document, Entity, QARecord, User
from ..schemas import (
    DashboardDocument,
    DashboardEntity,
    DashboardOut,
    DashboardQA,
    GapReport,
    InsightOut,
    SettingsOut,
    SettingsUpdate,
    UserOut,
)
from ..services.gaps import analyze_gaps
from ..services.insight import daily_insight
from ..services.settings import get_settings, update_settings
from .graph import _compute_stats, _degree_map, _doc_count_map, _owner_clause

router = APIRouter(prefix="/api/me", tags=["me"])

TOP_ENTITY_LIMIT = 5
RECENT_DOC_LIMIT = 5
RECENT_QA_LIMIT = 3


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """「我的主页」数据:概览、我关注的实体、热门实体、最近文档与问答。"""
    owner_id = user.id
    stats = _compute_stats(db, owner_id)
    degrees = _degree_map(db, owner_id)
    doc_counts = _doc_count_map(db, owner_id)

    def to_entity(entity: Entity) -> DashboardEntity:
        return DashboardEntity(
            id=entity.id,
            name=entity.name,
            type=entity.type,
            status=entity.status,
            degree=degrees.get(entity.id, 0),
            doc_count=doc_counts.get(entity.id, 0),
            starred=bool(entity.starred),
        )

    starred = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.starred.is_(True))
        .filter(Entity.status != "rejected")
        .all()
    )

    # 热门实体:先按热度(证据条数)粗筛,再按关系数排序,避免全表扫描
    candidates = (
        db.query(Entity)
        .filter(_owner_clause(Entity, owner_id))
        .filter(Entity.status != "rejected")
        .order_by(Entity.mention_count.desc(), Entity.name)
        .limit(50)
        .all()
    )
    top = sorted(candidates, key=lambda e: (-(degrees.get(e.id, 0)), e.name[:1]))
    top = top[:TOP_ENTITY_LIMIT]

    recent_documents = (
        db.query(Document)
        .filter(_owner_clause(Document, owner_id))
        .order_by(Document.created_at.desc())
        .limit(RECENT_DOC_LIMIT)
        .all()
    )
    recent_qa = (
        db.query(QARecord)
        .filter(_owner_clause(QARecord, owner_id))
        .order_by(QARecord.created_at.desc())
        .limit(RECENT_QA_LIMIT)
        .all()
    )

    created = user.created_at or datetime.utcnow()
    joined_days = max((datetime.utcnow() - created).days + 1, 1)

    return DashboardOut(
        user=UserOut(id=user.id, username=user.username, created_at=user.created_at),
        stats=stats,
        joined_days=joined_days,
        starred_entities=[to_entity(e) for e in starred],
        top_entities=[to_entity(e) for e in top],
        recent_documents=[
            DashboardDocument(
                id=d.id,
                title=d.title,
                file_type=d.file_type,
                status=d.status,
                created_at=d.created_at,
            )
            for d in recent_documents
        ],
        recent_qa=[
            DashboardQA(id=q.id, question=q.question, created_at=q.created_at)
            for q in recent_qa
        ],
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读取个性化设置(问答风格 / 语言 / 是否引用证据 / 问答范围)。"""
    return get_settings(db, user.id)


@router.put("/settings", response_model=SettingsOut)
def write_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """局部更新个性化设置;取值不合法返回 400。"""
    try:
        return update_settings(db, user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gaps", response_model=GapReport)
def gaps(
    space_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """知识缺口:孤岛实体 / 单来源实体 / 共现却没有关系的实体对。"""
    return analyze_gaps(db, user.id, space_id)


@router.get("/insight", response_model=InsightOut)
def insight(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """今日洞察:同一天只生成一次,之后直接读缓存。"""
    record = daily_insight(db, user.id)
    return InsightOut(
        day=record.day,
        content=record.content,
        model_available=str(record.model_available).lower() == "true",
    )
