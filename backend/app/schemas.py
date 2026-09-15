"""接口出入参定义。"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------- 文档 ----------------
class DocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    file_type: str
    status: str
    error: str = ""
    chunk_count: int = 0
    space_id: Optional[int] = None
    created_at: datetime
    # 耗时预估:前端据此显示「预计需要多久」,并判断要不要先问用户
    est_seconds: float = 0.0      # 完整抽取预计耗时(秒)
    fast_seconds: float = 0.0     # 快速抽取预计耗时(秒)
    heavy: bool = False           # 完整抽取是否算「偏长」

    class Config:
        from_attributes = True


# ---------------- 图 ----------------
class EntityOut(BaseModel):
    id: int
    name: str
    type: str
    description: str = ""
    status: str
    starred: bool = False

    class Config:
        from_attributes = True


class RelationOut(BaseModel):
    id: int
    source_id: int
    target_id: int
    relation_type: str
    description: str = ""
    status: str

    class Config:
        from_attributes = True


class GraphNode(BaseModel):
    id: int
    name: str
    type: str
    status: str
    degree: int = 0
    doc_count: int = 0
    starred: bool = False


class GraphEdge(BaseModel):
    id: int
    source: int
    target: int
    relation_type: str
    description: str = ""
    status: str


class GraphData(BaseModel):
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []


class EntityCreate(BaseModel):
    name: str
    type: str = "concept"
    description: str = ""


class EntityUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    starred: Optional[bool] = None


class RelationCreate(BaseModel):
    source_id: int
    target_id: int
    relation_type: str = "related_to"
    description: str = ""
    status: str = "confirmed"


class RelationUpdate(BaseModel):
    relation_type: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


# ---------------- 证据与检索 ----------------
class EvidenceOut(BaseModel):
    id: int
    source_type: str
    source_id: int
    document_id: int
    document_title: str = ""
    chunk_id: int
    locator: str = ""
    quote: str = ""


class EntityDetail(BaseModel):
    entity: EntityOut
    neighbors: List[GraphNode] = []
    edges: List[GraphEdge] = []
    evidence: List[EvidenceOut] = []


class SearchResult(BaseModel):
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    evidence: List[EvidenceOut] = []


# ---------------- 待确认草稿 ----------------
class ReviewQueue(BaseModel):
    entities: List[EntityOut] = []
    relations: List[RelationOut] = []


# ---------------- 问答 ----------------
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    max_hops: int = 2
    space_id: Optional[int] = None


class AskResponse(BaseModel):
    record_id: int
    question: str
    answer: str
    concepts: List[str] = []
    paths: List[List[str]] = []
    evidence: List[EvidenceOut] = []
    model_available: bool = False


class UndoBatch(BaseModel):
    """批量撤销:一次恢复多条被删记录。"""
    ids: List[int] = []


class QARecordOut(BaseModel):
    id: int
    question: str
    answer: str
    model_available: str
    created_at: datetime

    class Config:
        from_attributes = True


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    created_at: datetime

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    token: str
    user: UserOut
    claimed_orphans: int = 0


# ---------------- 个人主页 ----------------
class DashboardDocument(BaseModel):
    id: int
    title: str
    file_type: str
    status: str
    created_at: datetime


class DashboardEntity(BaseModel):
    id: int
    name: str
    type: str
    status: str
    degree: int = 0
    doc_count: int = 0
    starred: bool = False


class DashboardQA(BaseModel):
    id: int
    question: str
    created_at: datetime


class DashboardOut(BaseModel):
    """「我的主页」:一次拿全概览、关注、最近动态与待办。"""

    user: UserOut
    stats: Dict[str, int] = {}
    joined_days: int = 0
    starred_entities: List[DashboardEntity] = []
    top_entities: List[DashboardEntity] = []
    recent_documents: List[DashboardDocument] = []
    recent_qa: List[DashboardQA] = []


# ---------------- 知识空间 ----------------
class SpaceCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#4f8ef7"


class SpaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


class SpaceOut(BaseModel):
    id: int
    name: str
    description: str = ""
    color: str = "#4f8ef7"
    document_count: int = 0
    entity_count: int = 0
    created_at: datetime


class DocumentSpaceUpdate(BaseModel):
    """把文档归入某个空间;space_id 为 None 表示移回「未归档」。"""

    space_id: Optional[int] = None


# ---------------- 用户偏好(问答) ----------------
class SettingsOut(BaseModel):
    answer_style: str = "concise"        # concise / detailed
    answer_language: str = "zh"          # zh / en
    cite_evidence: bool = True           # 是否在答案里逐条引用原文证据
    qa_scope: str = "all"                # all / current_space
    # 自定义抽取维度:空列表表示不限制
    extract_entity_types: List[str] = []
    extract_relation_types: List[str] = []
    extract_focus: str = ""              # 关注领域描述,会注入抽取 prompt


class SettingsUpdate(BaseModel):
    answer_style: Optional[str] = None
    answer_language: Optional[str] = None
    cite_evidence: Optional[bool] = None
    qa_scope: Optional[str] = None
    extract_entity_types: Optional[List[str]] = None
    extract_relation_types: Optional[List[str]] = None
    extract_focus: Optional[str] = None


# ---------------- 每日洞察 ----------------
class InsightOut(BaseModel):
    day: str
    content: str
    model_available: bool = False


# ---------------- 知识缺口 ----------------
class GapItem(BaseModel):
    id: int
    name: str
    type: str = ""
    doc_count: int = 0


class MissingLink(BaseModel):
    source_id: int
    source: str
    target_id: int
    target: str
    co_occurrences: int = 1


class GapReport(BaseModel):
    isolated: List[GapItem] = []            # 有证据但没有任何关系
    weak_evidence: List[GapItem] = []       # 只在一篇文档出现过
    missing_links: List[MissingLink] = []   # 常共现却没有直接关系
    pending_entities: int = 0
    pending_relations: int = 0
