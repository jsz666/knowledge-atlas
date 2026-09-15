"""数据库迁移测试:旧版本库升级时不丢数据。

用独立的临时库直接验证 apply_schema_migrations,不依赖全局 engine。
运行方式(在 backend 目录):
    python3 -m pytest tests -q
"""

import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.database import apply_schema_migrations  # noqa: E402


def _temp_engine():
    path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    return create_engine(f"sqlite:///{path}")


def test_migration_adds_missing_columns_and_keeps_data():
    engine = _temp_engine()

    with engine.begin() as conn:
        # 模拟旧版本:entities 还没有 mention_count / updated_at
        conn.execute(text("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, type TEXT)"))
        conn.execute(text("INSERT INTO entities (id, name, type) VALUES (1, 'Transformer', 'method')"))

    with engine.begin() as conn:
        apply_schema_migrations(conn)

    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(entities)"))}
        assert "mention_count" in columns, "应补上实体热度列"
        assert "updated_at" in columns, "应补上更新时间列"
        # 原有数据必须完好
        assert (
            conn.execute(text("SELECT name FROM entities WHERE id = 1")).scalar()
            == "Transformer"
        )


def test_migration_is_idempotent():
    engine = _temp_engine()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)"))

    # 连续执行三次都不应报错(重复加列会被跳过)
    for _ in range(3):
        with engine.begin() as conn:
            apply_schema_migrations(conn)

    with engine.begin() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info(entities)"))]
        assert columns.count("mention_count") == 1


def test_migration_skips_absent_tables():
    engine = _temp_engine()

    with engine.begin() as conn:
        # 一张表都没有(全新库场景),不应抛异常
        apply_schema_migrations(conn)
