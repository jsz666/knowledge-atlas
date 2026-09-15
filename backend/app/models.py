"""数据模型。

设计要点:每条实体/关系都通过 evidence 表指回具体的原文片段(chunk),
保证「来源可追踪」;status 字段支撑人工确认闭环。

完整性约定:
- 删除文档会级联删除它的片段与证据(ondelete="CASCADE",需配合 SQLite 的
  PRAGMA foreign_keys=ON,见 database.py);
- 删除实体会级联删除挂在它身上的关系、证据与别名;
- mention_count 冗余保存实体热度(证据条数),供图谱按重要性渲染节点大小。

归属约定(多用户隔离):
- documents / entities / relations / evidence / qa_records / trash 都带 owner_id;
- owner_id 为 NULL 表示「无主的历史数据」,首个注册的用户会自动接管它们;
- chunks 与 entity_aliases 通过外键间接归属,不单独存 owner_id。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .database import Base


class User(Base):
    """账号:用户名 + 密码哈希(pbkdf2),不存明文密码。"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    salt = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Space(Base):
    """知识空间:把文档按主题 / 项目分组。

    实体不直接属于空间 —— 实体与空间的关系通过「证据 → 文档」间接推导,
    这样一个概念仍然可以跨文档、跨空间复用,只是「在某个空间里能看到哪些实体」不同。
    """

    __tablename__ = "spaces"
    __table_args__ = (UniqueConstraint("name", "owner_id", name="uq_space_name"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    color = Column(String, default="#4f8ef7")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)          # pdf / markdown / txt
    storage_path = Column(String, nullable=False)
    status = Column(String, default="pending")          # pending/parsed/extracted/failed
    error = Column(Text, default="")
    space_id = Column(
        Integer, ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    locator = Column(String, default="")                # 页码 / 章节标题
    content = Column(Text, nullable=False)


class Entity(Base):
    __tablename__ = "entities"
    # 同名实体按归属区分:不同用户可以各有一套自己的图谱
    __table_args__ = (
        UniqueConstraint("name", "type", "owner_id", name="uq_entity_name_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    type = Column(String, default="concept")            # concept/method/person/paper/tool...
    description = Column(Text, default="")
    status = Column(String, default="pending")          # pending/confirmed/rejected
    mention_count = Column(Integer, default=0, nullable=False)  # 证据条数,即热度
    starred = Column(Boolean, default=False, nullable=False)    # 用户星标(我的关注)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "target_id", "relation_type", "owner_id", name="uq_relation"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type = Column(String, default="related_to")
    description = Column(Text, default="")
    status = Column(String, default="pending")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Evidence(Base):
    """来源追踪:实体或关系出自哪篇文档的哪个片段、原文是什么。"""

    __tablename__ = "evidence"

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String, nullable=False)        # entity / relation
    source_id = Column(Integer, nullable=False, index=True)
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_id = Column(
        Integer,
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quote = Column(Text, default="")                    # 原文摘录,便于前端直接展示
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)


class EntityAlias(Base):
    """实体别名:消歧合并时被并入的旧名字。

    合并后旧实体被删除,但旧名字保留在这里,保证:
    - 用旧名字搜索仍能命中合并后的实体;
    - 追溯「这个实体曾经叫什么」。
    """

    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),)

    id = Column(Integer, primary_key=True, index=True)
    entity_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class QARecord(Base):
    """问答历史与证据快照,用于回看和验收展示。"""

    __tablename__ = "qa_records"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, default="")
    concepts = Column(Text, default="[]")               # JSON 字符串
    paths = Column(Text, default="[]")                  # JSON 字符串
    evidences = Column(Text, default="[]")              # JSON 字符串
    model_available = Column(String, default="false")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSetting(Base):
    """用户级配置(KV 存储),目前用于问答偏好(风格 / 语言 / 是否引用证据)。"""

    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_setting"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key = Column(String, nullable=False)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Share(Base):
    """只读分享:把某个知识空间用令牌公开给他人,对方无需登录,也不能写入。"""

    __tablename__ = "shares"

    id = Column(Integer, primary_key=True)
    kind = Column(String, default="space")          # 目前只支持分享空间
    target_id = Column(Integer, nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Boolean, default=False, nullable=False)
    view_count = Column(Integer, default=0, nullable=False)


class Insight(Base):
    """每日洞察缓存:同一用户同一天只生成一次,避免重复调用模型。"""

    __tablename__ = "insights"
    __table_args__ = (UniqueConstraint("owner_id", "day", name="uq_insight_day"),)

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day = Column(String, nullable=False, index=True)     # YYYY-MM-DD
    content = Column(Text, default="")
    model_available = Column(String, default="false")
    fingerprint = Column(String, default="")            # 数据指纹:底层数据变了就让缓存失效
    created_at = Column(DateTime, default=datetime.utcnow)


class Trash(Base):
    """删除快照:删除实体/关系前先存快照(含证据),用于撤销恢复。"""

    __tablename__ = "trash"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False)                # entity / relation
    label = Column(String, default="")                   # 展示用名称
    stack = Column(String, default="undo")               # undo / redo
    snapshot = Column(Text, nullable=False)              # JSON
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
