"""文档管理:上传、列表、片段浏览、重新抽取、删除,以及快照导入。

归属规则:写入(上传 / 重抽 / 删除 / 导入)必须登录,且只能操作自己的文档;
读取(列表 / 片段)允许匿名,但只能看到无主的公共数据。
"""

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..database import get_db
from ..deps import get_current_user, get_optional_user
from ..models import Chunk, Document, Evidence, Space, User
from ..schemas import DocumentOut, DocumentSpaceUpdate
from ..services import importer, progress
from ..services.graph_store import recalc_mention_counts
from ..services.parser import detect_file_type
from ..services.pipeline import extract_document, parse_document, prune_orphans

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _owner_clause(model, owner_id):
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _document_out(db: Session, document: Document) -> DocumentOut:
    chunk_count = (
        db.query(func.count(Chunk.id)).filter(Chunk.document_id == document.id).scalar() or 0
    )
    est_seconds = config.estimate_extract_seconds(chunk_count)
    return DocumentOut(
        id=document.id,
        title=document.title,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        error=document.error or "",
        chunk_count=chunk_count,
        space_id=document.space_id,
        created_at=document.created_at,
        est_seconds=est_seconds,
        fast_seconds=config.estimate_extract_seconds(chunk_count, fast=True),
        heavy=est_seconds > config.EXTRACT_HEAVY_SECONDS,
    )


def _load_own_document(db: Session, document_id: int, owner_id) -> Document:
    """按归属取文档:取不到或不属于当前用户一律 404(不暴露他人数据是否存在)。"""
    document = db.get(Document, document_id)
    if document is None or document.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


@router.post("/upload", response_model=DocumentOut)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传一篇文档并解析分块(不调用模型,所以响应很快)。

    抽取拆成了单独一步:前端先拿到「共 N 块 / 预计耗时多久」,若预计耗时过长
    就先让用户选完整还是快速抽取,再调用 /extract,而不是在这里闷头跑十几分钟。

    用同步 def,FastAPI 会放到线程池执行,避免解析大文件时阻塞事件循环。
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {extension},仅支持 {', '.join(sorted(config.ALLOWED_EXTENSIONS))}",
        )

    safe_name = Path(file.filename or "document").name
    storage_name = f"{uuid.uuid4().hex}_{safe_name}"
    storage_path = config.UPLOAD_DIR / storage_name

    with storage_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    document = Document(
        title=Path(safe_name).stem,
        filename=safe_name,
        file_type=detect_file_type(safe_name),
        storage_path=str(storage_path),
        status="pending",
        owner_id=user.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    parse_document(db, document)
    db.refresh(document)
    return _document_out(db, document)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    owner_id = user.id if user else None
    documents = (
        db.query(Document)
        .filter(_owner_clause(Document, owner_id))
        .order_by(Document.created_at.desc())
        .all()
    )
    return [_document_out(db, doc) for doc in documents]


@router.get("/progress")
def extraction_progress(user: User = Depends(get_current_user)):
    """正在抽取的文档的实时进度:已用时间与预计剩余时间都据此计算。

    纯内存读取,刻意不碰数据库 —— 抽取正在进行时会持有写事务,轮询若查库
    会和它抢锁,把界面卡住。
    """
    return {"jobs": progress.summary(user.id)}


@router.get("/{document_id}/chunks")
def list_chunks(
    document_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """某篇文档的全部原文片段,用于从证据回看上下文。"""
    document = _load_own_document(db, document_id, user.id if user else None)

    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
        .all()
    )
    return {
        "document": {
            "id": document.id,
            "title": document.title,
            "file_type": document.file_type,
            "status": document.status,
        },
        "chunks": [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "locator": chunk.locator or "",
                "content": chunk.content,
            }
            for chunk in chunks
        ],
    }


@router.post("/{document_id}/extract", response_model=DocumentOut)
def reextract_document(
    document_id: int,
    fast: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """抽取 / 重新抽取一篇文档。

    fast=True 只处理前若干块,用覆盖面换时间,适合先把图谱跑起来看效果。
    清空旧结果与重抽在同一个事务里:抽取失败整体回滚,旧结果不会被白白删掉。
    """
    document = _load_own_document(db, document_id, user.id)
    extract_document(db, document, fast=fast)
    db.refresh(document)
    return _document_out(db, document)


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = _load_own_document(db, document_id, user.id)

    storage_path = Path(document.storage_path)
    if storage_path.exists():
        storage_path.unlink()

    # 旧库外键可能没有 ON DELETE CASCADE,这里显式清理,否则删除文档会因外键约束失败
    chunk_ids = [c.id for c in db.query(Chunk).filter(Chunk.document_id == document_id).all()]
    if chunk_ids:
        db.query(Evidence).filter(Evidence.chunk_id.in_(chunk_ids)).delete(
            synchronize_session=False
        )
    db.query(Evidence).filter(Evidence.document_id == document_id).delete(
        synchronize_session=False
    )
    db.query(Chunk).filter(Chunk.document_id == document_id).delete(synchronize_session=False)
    db.delete(document)
    db.commit()
    prune_orphans(db, user.id)
    recalc_mention_counts(db, owner_id=user.id)
    db.commit()
    return {"deleted": document_id}


@router.patch("/{document_id}/space", response_model=DocumentOut)
def set_document_space(
    document_id: int,
    payload: DocumentSpaceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把文档归入某个知识空间;space_id 为 null 表示移回「未归档」。"""
    document = _load_own_document(db, document_id, user.id)

    if payload.space_id is not None:
        space = db.get(Space, payload.space_id)
        if space is None or space.owner_id != user.id:
            raise HTTPException(status_code=404, detail="空间不存在")

    document.space_id = payload.space_id
    db.commit()
    db.refresh(document)
    return _document_out(db, document)


@router.post("/import")
async def import_snapshot(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """导入 JSON 快照,合并进当前用户的图谱(重复导入不会产生重复数据)。"""
    filename = (file.filename or "").lower()
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="请上传导出的 JSON 快照文件")

    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 解析失败,文件可能已损坏")

    if not isinstance(data, dict) or "entities" not in data:
        raise HTTPException(
            status_code=400,
            detail="这不是本项目的导出快照(缺少 entities 字段)",
        )

    try:
        stats = importer.import_snapshot(db, data, owner_id=user.id)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"导入失败:{exc}") from exc

    return {"imported": stats}
