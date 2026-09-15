"""开发辅助接口:灌入 / 清空演示数据(需登录,数据只属于当前用户)。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..services.seed import reset_mock_data, seed_mock_data

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.post("/seed")
def seed(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """为当前用户灌入示例知识星图(该账号已有数据时跳过)。"""
    return seed_mock_data(db, owner_id=user.id)


@router.post("/reset")
def reset(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """清空当前用户的全部数据(不影响其他账号)。"""
    reset_mock_data(db, owner_id=user.id)
    return {"reset": True}
