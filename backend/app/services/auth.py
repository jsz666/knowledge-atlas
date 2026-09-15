"""账号体系:密码哈希与访问令牌。

刻意不使用第三方依赖:
- 口令用标准库 pbkdf2_hmac(sha256) 加盐派生,不存明文;
- 令牌是 HMAC-SHA256 签名的自包含串(有效载荷 + 签名),服务端无需存会话。

如需更强的口令哈希,可换成 passlib[bcrypt],接口保持不变。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from .. import config

_ITERATIONS = 120_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (salt, password_hash)。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS
    )
    return salt, digest.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash or "")


def _sign(body: str) -> str:
    return hmac.new(
        config.AUTH_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(body: str) -> bytes:
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))


def create_token(user_id: int, username: str) -> str:
    """签发令牌:有效载荷里只放用户标识与过期时间。"""
    payload = {
        "uid": user_id,
        "sub": username,
        "exp": int(time.time()) + config.TOKEN_TTL_HOURS * 3600,
    }
    body = _b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def decode_token(token: str) -> dict | None:
    """校验签名与过期时间,失败返回 None。"""
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    if not body or not signature:
        return None
    if not hmac.compare_digest(_sign(body), signature):
        return None
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload
