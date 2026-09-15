"""FastAPI 应用入口。"""

import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import DATABASE_PATH, OPENAI_MODEL
from .database import ensure_schema
from .routers import auth, documents, graph, me, qa, dev, export, shared, spaces
from .services.llm import get_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表 + 旧库补齐列/索引(create_all 不会修改已存在的表)
    ensure_schema()
    llm = get_llm()
    logger.info("数据库:%s", DATABASE_PATH)
    logger.info(
        "模型状态:%s(%s)",
        "已接入" if llm.available else "离线兜底模式",
        OPENAI_MODEL,
    )
    yield


app = FastAPI(
    title="知识星图 API",
    description="文档关系探索器:实体关系抽取、图探索与跨文档证据问答",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 本地开发用,部署时改成前端域名
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(graph.router)
app.include_router(qa.router)
app.include_router(export.router)
app.include_router(dev.router)
app.include_router(me.router)
app.include_router(spaces.router)
app.include_router(shared.router)


@app.get("/api/health")
def health():
    llm = get_llm()
    return {
        "status": "ok",
        "model_available": llm.available,
        "model": OPENAI_MODEL,
        "database": str(DATABASE_PATH),
    }
