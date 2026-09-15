"""全局配置。

所有敏感信息(模型密钥)只从这里读取,来源为 backend/.env,
永远不会进入前端代码或版本库。
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> parents[0]=app, [1]=backend, [2]=项目根
BASE_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = BASE_DIR / "backend"

load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "knowledge_atlas.db"

for _dir in (DATA_DIR, UPLOAD_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")


# ---------- 账号与令牌 ----------
def _load_auth_secret() -> str:
    """读取令牌签名密钥。

    优先用 .env 里的 AUTH_SECRET;否则生成一份并持久化到 data/.auth_secret,
    避免服务重启后所有已登录用户被登出。
    """
    from_env = os.getenv("AUTH_SECRET", "").strip()
    if from_env:
        return from_env

    secret_file = DATA_DIR / ".auth_secret"
    if secret_file.exists():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value

    value = secrets.token_hex(32)
    secret_file.write_text(value, encoding="utf-8")
    return value


AUTH_SECRET = _load_auth_secret()
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "72"))

# ---------- 模型配置 ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
# 单次调用的输出上限(包含推理模型的「思考」token)。推理模型思考同样占用这份
# 预算:设得太小会导致正文为空,进而把整块文档降级成规则抽取。
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8000"))

# ---------- 抽取规模控制 ----------
MAX_CHUNKS_PER_DOC = int(os.getenv("MAX_CHUNKS_PER_DOC", "40"))
CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "900"))

# 单块抽取的输出预算(含推理模型的「思考」token)。推理模型的思考同样占用这份
# 预算:一次给足,比「先给少了→正文为空→翻倍重试」少一次网络往返。
LLM_EXTRACT_MAX_TOKENS = int(os.getenv("LLM_EXTRACT_MAX_TOKENS", "4000"))

# 抽取并发数。抽取是网络 I/O 密集,并发可近似线性压缩总耗时;过高会撞上端点
# 限流,默认 4。设为 1 即退回串行(便于对照排查)。
EXTRACT_CONCURRENCY = int(os.getenv("EXTRACT_CONCURRENCY", "4"))

# 单块抽取的典型耗时(秒)。抽取还没跑完时先用它给出「预计耗时」,跑起来后
# 会被实测速度自动替换,所以估偏了也能自我纠正。
EXTRACT_SECONDS_PER_CHUNK = float(os.getenv("EXTRACT_SECONDS_PER_CHUNK", "7"))

# 预计耗时超过这个秒数就算「偏长」,前端会先让用户选完整抽取还是快速抽取。
EXTRACT_HEAVY_SECONDS = float(os.getenv("EXTRACT_HEAVY_SECONDS", "60"))

# 快速抽取只处理前 N 块:用覆盖面换时间,适合先把图谱跑起来看效果。
EXTRACT_FAST_MAX_CHUNKS = int(os.getenv("EXTRACT_FAST_MAX_CHUNKS", "12"))


def extract_chunk_limit(fast: bool = False) -> int:
    """本次抽取最多处理多少块。快速模式砍掉尾部,换取成倍的时间节省。"""
    return EXTRACT_FAST_MAX_CHUNKS if fast else MAX_CHUNKS_PER_DOC


def estimate_extract_seconds(chunk_count: int, fast: bool = False) -> float:
    """按块数估算抽取耗时(秒)。

    并发把总耗时摊薄到约「块数 / 并发数」个单块耗时;并发数不会超过块数本身
    (块数比并发数还少时,再多的并发也快不了)。
    """
    total = min(max(chunk_count, 0), extract_chunk_limit(fast))
    if total <= 0:
        return 0.0
    workers = max(1, min(EXTRACT_CONCURRENCY, total))
    return round(total / workers * EXTRACT_SECONDS_PER_CHUNK, 1)

# 允许的扩展名
ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}

# 关系类型白名单:AI 输出必须落在这里面,否则降级为 related_to
RELATION_TYPES = [
    "related_to",      # 泛化关联
    "derives_from",    # 由…演化/继承而来
    "improves_on",     # 在…基础上改进
    "part_of",         # 组成部分
    "uses",            # 使用/依赖
    "compared_with",   # 对比
    "proposed_by",     # 由谁提出
]
DEFAULT_RELATION_TYPE = "related_to"
