"""SQLite 连接与会话管理。

注意两点(否则「级联删除」「并发写入」都会静默失效):
1. SQLite 默认关闭外键约束,必须每个连接执行 PRAGMA foreign_keys=ON,
   否则模型里写的 ondelete="CASCADE" 不会生效,删文档会留下孤儿片段。
2. 开启 WAL 让读写不互相阻塞,并设置 busy_timeout 避免并发时直接报 database is locked。
"""

import re

from sqlalchemy import (
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from .config import DATABASE_URL

IS_SQLITE = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


if IS_SQLITE:

    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


# 旧库平滑升级:create_all 只建新表,不会给已有表补列,这里手工补齐。
_COLUMN_MIGRATIONS = (
    ("entities", "mention_count", "INTEGER NOT NULL DEFAULT 0"),
    ("entities", "updated_at", "DATETIME"),
    ("relations", "updated_at", "DATETIME"),
    ("documents", "updated_at", "DATETIME"),
    # 多用户归属:NULL 表示历史无主数据,首个注册用户会接管
    ("documents", "owner_id", "INTEGER"),
    ("entities", "owner_id", "INTEGER"),
    ("relations", "owner_id", "INTEGER"),
    ("evidence", "owner_id", "INTEGER"),
    ("qa_records", "owner_id", "INTEGER"),
    ("trash", "owner_id", "INTEGER"),
    # 个性化:实体星标(我的关注)
    ("entities", "starred", "BOOLEAN NOT NULL DEFAULT 0"),
    # 知识空间:文档归入空间(空间表由 create_all 新建)
    ("documents", "space_id", "INTEGER"),
    # 每日洞察缓存的数据指纹:缺列时旧缓存会因指纹为空而自动重新生成
    ("insights", "fingerprint", "TEXT"),
)

# (表名, 建索引语句):表不存在时跳过,避免旧库缺表时建索引报错
_INDEX_MIGRATIONS = (
    (
        "evidence",
        "CREATE INDEX IF NOT EXISTS ix_evidence_source_type_id"
        " ON evidence (source_type, source_id)",
    ),
)


def apply_schema_migrations(connection) -> bool:
    """给已有表补齐新增列与索引(幂等,可重复执行)。

    返回是否真的补过列,便于调用方判断要不要补算派生数据。
    """
    changed = False

    for table, column, ddl in _COLUMN_MIGRATIONS:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if not rows:  # 表不存在(全新库已由 create_all 建好),跳过
            continue
        if column not in {row[1] for row in rows}:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            changed = True

    for table, statement in _INDEX_MIGRATIONS:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if not rows:  # 表不存在(全新库已由 create_all 建好索引)
            continue
        connection.execute(text(statement))

    return changed


# 建表时写下的 UNIQUE 约束没法用 ALTER TABLE 改,而多用户改造只给 entities /
# relations 补了 owner_id 列(见 _COLUMN_MIGRATIONS),约束却还停在「(name, type)」
# 这种不含 owner_id 的老定义上。后果:第二个用户抽到同名实体时,去重查询按
# owner_id 查不到、插入又被老约束拒绝,IntegrityError 直接把整个抽取打成 500。
# 这里按模型的定义把这两张表重建一次,让约束和代码对齐。


def _live_unique_groups(connection, table: str) -> set:
    """从 sqlite_master 读出这张表实际生效的 UNIQUE 约束(列名元组的集合)。"""
    ddl = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table},
    ).scalar()
    if not ddl:
        return set()
    return {
        tuple(column.strip().strip('"') for column in group.split(","))
        for group in re.findall(r"UNIQUE\s*\(([^)]*)\)", ddl)
    }


def _model_unique_groups(table) -> set:
    """模型里声明的 UNIQUE 约束(列名元组的集合)。"""
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _rebuild_table(cursor, table) -> None:
    """按模型定义重建表并原样搬运数据,顺带重建它的索引。

    顺序刻意是「建新表 → 搬数据 → 删旧表 → 改名」:若改成先给旧表改名,
    SQLite 会把别的表里 REFERENCES entities(id) 的指向一并改掉。
    """
    staging = f"{table.name}__rebuild"
    # 直接拿模型编译建表语句、只把表名换成中转名:这样不必把中转表塞进
    # Base.metadata(它还要被 create_all 使用),引用 users / entities 的外键也能正常解析
    ddl = str(CreateTable(table).compile(engine)).lstrip()
    prefix = f"CREATE TABLE {table.name} "
    if not ddl.startswith(prefix):
        raise RuntimeError(f"建表语句与预期不符,无法重建 {table.name}:{ddl.splitlines()[0]}")
    # 先只建表:索引沿用旧名字,等旧表删掉(连带它的索引)之后再建,避免撞名
    cursor.execute(f"CREATE TABLE {staging} " + ddl[len(prefix) :])
    columns = ", ".join(column.name for column in table.columns)
    cursor.execute(f"INSERT INTO {staging} ({columns}) SELECT {columns} FROM {table.name}")
    cursor.execute(f"DROP TABLE {table.name}")
    cursor.execute(f"ALTER TABLE {staging} RENAME TO {table.name}")
    for index in table.indexes:
        cursor.execute(str(CreateIndex(index).compile(engine)))


def _reconcile_unique_constraints() -> bool:
    """把和模型不一致的 UNIQUE 约束重建掉,返回是否动过库。"""
    from .models import Entity, Relation

    tables = (Entity.__table__, Relation.__table__)
    stale = []
    with engine.connect() as probe:
        for table in tables:
            live = _live_unique_groups(probe, table.name)
            # 表还不存在(全新库)或本来就没有唯一约束时不碰它,避免误重建
            if live and live != _model_unique_groups(table):
                stale.append(table)
    if not stale:
        return False

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        # 必须在事务开始前关外键:entities 的子表带 ondelete="CASCADE",
        # 外键开着时 DROP TABLE 会顺着级联把 relations / evidence 一起清空
        cursor.execute("PRAGMA foreign_keys=OFF")
        try:
            for table in stale:
                _rebuild_table(cursor, table)
            broken = cursor.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"重建后外键校验不通过:{broken[:3]}")
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()
    return True


def _needs_mention_backfill(connection) -> bool:
    """历史库:热度列已存在但从未计算过(全是 0,却已经有证据)。"""
    total = connection.execute(text("SELECT COUNT(*) FROM entities")).scalar() or 0
    if not total:
        return False
    filled = (
        connection.execute(text("SELECT COUNT(*) FROM entities WHERE mention_count > 0")).scalar()
        or 0
    )
    if filled:
        return False
    evidence = (
        connection.execute(
            text("SELECT COUNT(*) FROM evidence WHERE source_type = 'entity'")
        ).scalar()
        or 0
    )
    return evidence > 0


def ensure_schema() -> None:
    """建表并把旧库补齐到当前 schema,保证升级不丢数据。"""
    # 延迟导入:确保全部模型都注册到 Base 上,否则新表(如 entity_aliases)不会被创建
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # 约束漂移:ALTER TABLE 改不了 UNIQUE,得单独重建(见 _reconcile_unique_constraints)
    _reconcile_unique_constraints()

    with engine.begin() as connection:
        upgraded = apply_schema_migrations(connection)
        if not upgraded and _needs_mention_backfill(connection):
            upgraded = True

    if upgraded:
        # 旧库刚补上新列,派生的实体热度还是空的,补算一次
        from .services.graph_store import recalc_mention_counts

        db = SessionLocal()
        try:
            recalc_mention_counts(db)
            db.commit()
        finally:
            db.close()


def get_db():
    """FastAPI 依赖项:每个请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
