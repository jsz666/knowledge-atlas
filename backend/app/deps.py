"""认证依赖:从请求头解析令牌,得到当前用户。

约定:
- 只读接口用 get_optional_user(未登录也能看,但只能看到公共数据);
- 写接口用 get_current_user(未登录直接 401)。
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .services import auth


def _token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if value and scheme.lower() in {"bearer", "token"}:
        return value.strip()
    return authorization.strip() or None


def get_optional_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User | None:
    """解析成功返回用户,未登录 / 令牌失效返回 None。"""
    token = _token_from_header(authorization)
    if not token:
        return None

    payload = auth.decode_token(token)
    if not payload:
        return None
    return db.get(User, payload.get("uid"))


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    """必须登录:否则 401。"""
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录后再操作")
    return user
