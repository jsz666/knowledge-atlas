"""导出接口:JSON 快照 / CSV 清单 / Markdown 关系清单。

返回带 Content-Disposition 的响应,前端用普通链接即可触发下载。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_optional_user
from ..models import User
from ..services import exporter

router = APIRouter(prefix="/api/export", tags=["export"])

_STAMP = datetime.now().strftime("%Y%m%d")


@router.get("/json")
def export_json(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """完整快照(只导出当前账号的数据)。"""
    content = exporter.build_json(db, owner_id=user.id if user else None)
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="knowledge-atlas-{_STAMP}.json"'
        },
    )


@router.get("/csv")
def export_csv(
    kind: str = "entities",
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """实体或关系表格(kind=entities | relations),只导出当前账号的数据。"""
    if kind not in {"entities", "relations"}:
        raise HTTPException(status_code=400, detail="kind 只能是 entities 或 relations")

    # 加 BOM,Excel 打开中文不乱码
    owner_id = user.id if user else None
    content = "\ufeff" + exporter.build_csv(db, kind, owner_id=owner_id)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="knowledge-atlas-{kind}-{_STAMP}.csv"'
        },
    )


@router.get("/markdown")
def export_markdown(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """可读的关系清单(含证据摘录),只导出当前账号的数据。"""
    content = exporter.build_markdown(db, owner_id=user.id if user else None)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="knowledge-atlas-{_STAMP}.md"'
        },
    )
