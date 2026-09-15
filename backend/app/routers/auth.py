"""账号接口:注册 / 登录 / 当前用户 / 退出。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Document, Entity, Evidence, QARecord, Relation, Trash, User
from ..schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut
from ..services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

MIN_PASSWORD = 6
MIN_USERNAME = 2


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, created_at=user.created_at)


def _claim_orphans(db: Session, user: User) -> int:
    """首个注册的用户接管历史无主数据(owner_id IS NULL)。

    老库在引入账号之前没有归属字段,若不管这些行,它们会变成谁都看不到的孤儿。
    """
    others = db.query(func.count(User.id)).filter(User.id != user.id).scalar() or 0
    if others:
        return 0

    claimed = 0
    for model in (Document, Entity, Relation, Evidence, QARecord, Trash):
        claimed += (
            db.query(model)
            .filter(model.owner_id.is_(None))
            .update({"owner_id": user.id}, synchronize_session=False)
        )
    return claimed


@router.post("/register", response_model=AuthResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    username = (payload.username or "").strip()
    password = payload.password or ""

    if len(username) < MIN_USERNAME:
        raise HTTPException(status_code=400, detail=f"用户名至少 {MIN_USERNAME} 个字符")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail=f"密码至少 {MIN_PASSWORD} 位")
    if db.query(User).filter(User.username == username).one_or_none():
        raise HTTPException(status_code=400, detail="该用户名已被注册")

    salt, password_hash = auth.hash_password(password)
    user = User(username=username, password_hash=password_hash, salt=salt)
    db.add(user)
    db.flush()

    claimed = _claim_orphans(db, user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        token=auth.create_token(user.id, user.username),
        user=_user_out(user),
        claimed_orphans=claimed,
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    username = (payload.username or "").strip()
    user = db.query(User).filter(User.username == username).one_or_none()

    # 用户不存在与密码错误返回同样的提示,避免暴露用户名是否注册过
    if user is None or not auth.verify_password(
        payload.password or "", user.salt, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")

    return AuthResponse(
        token=auth.create_token(user.id, user.username), user=_user_out(user)
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.post("/logout")
def logout():
    """令牌本身无状态,前端丢弃即可;保留端点便于以后加黑名单。"""
    return {"logged_out": True}
