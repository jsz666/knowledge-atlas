"""临时脚本:真实库被测试污染过,整体重置后建一个可直接登录的 demo 账号。"""

from sqlalchemy import text

from app.database import SessionLocal, ensure_schema
from app.models import User
from app.services import auth
from app.services.seed import seed_mock_data

ensure_schema()
db = SessionLocal()

# 1) 清空全部业务数据与账号(测试曾误跑在真实库上)
for table in (
    "evidence",
    "relations",
    "entity_aliases",
    "qa_records",
    "trash",
    "chunks",
    "entities",
    "documents",
    "users",
):
    db.execute(text(f"DELETE FROM {table}"))
db.commit()

# 2) 建 demo 账号
salt, password_hash = auth.hash_password("demo12345")
demo = User(username="demo", password_hash=password_hash, salt=salt)
db.add(demo)
db.commit()

# 3) 给 demo 灌入示例星图
print("demo user id:", demo.id)
print("seed:", seed_mock_data(db, owner_id=demo.id))
print("users:", [u.username for u in db.query(User).all()])
db.close()
