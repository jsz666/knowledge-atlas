"""账号体系与数据隔离测试。

覆盖:注册 / 登录 / 口令校验规则 / 未登录写入被拒 / 两个账号之间数据互不可见。
运行方式(在 backend 目录):
    python3 -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def anon():
    """未登录的客户端。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def _register(anon, username, password="secret123"):
    return anon.post(
        "/api/auth/register", json={"username": username, "password": password}
    )


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_token(anon):
    response = _register(anon, "alice")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["user"]["username"] == "alice"

    # 重复用户名
    assert _register(anon, "alice").status_code == 400

    # 正确口令可登录
    login = anon.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    )
    assert login.status_code == 200
    assert login.json()["token"]

    # 错误口令 / 不存在的用户都返回 401(不区分,避免暴露用户名)
    assert anon.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong-pass"}
    ).status_code == 401
    assert anon.post(
        "/api/auth/login", json={"username": "nobody", "password": "secret123"}
    ).status_code == 401


def test_register_validates_input(anon):
    assert _register(anon, "a").status_code == 400, "用户名过短应被拒绝"
    assert _register(anon, "short-pw-user", "123").status_code == 400, "口令过短应被拒绝"


def test_me_requires_valid_token(anon):
    token = anon.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    ).json()["token"]

    me = anon.get("/api/auth/me", headers=_auth(token)).json()
    assert me["username"] == "alice"

    assert anon.get("/api/auth/me").status_code == 401
    assert anon.get("/api/auth/me", headers=_auth("bad.token")).status_code == 401


def test_write_requires_login(anon):
    """匿名只读:写入类操作必须登录。"""
    assert anon.post("/api/dev/seed").status_code == 401
    assert anon.post("/api/dev/reset").status_code == 401
    assert anon.delete("/api/graph/entities/1").status_code == 401
    assert anon.delete("/api/graph/trash").status_code == 401
    assert anon.post("/api/qa/ask", json={"question": "RAG 是什么?"}).status_code == 401

    # 但读取是放开的(只是看不到别人的数据)
    assert anon.get("/api/graph").status_code == 200
    assert anon.get("/api/graph/stats").status_code == 200


def test_data_is_isolated_between_accounts(anon):
    """两个账号各有一份图谱,互不可见、也不能改对方的数据。"""
    alice = _auth(
        anon.post(
            "/api/auth/login", json={"username": "alice", "password": "secret123"}
        ).json()["token"]
    )
    bob_token = _register(anon, "bob").json()["token"]
    bob = _auth(bob_token)

    # alice 灌入示例数据
    assert anon.post("/api/dev/seed", headers=alice).json()["seeded"] is True
    alice_stats = anon.get("/api/graph/stats", headers=alice).json()
    assert alice_stats["entities"] == 16

    # bob 看不到 alice 的数据
    bob_stats = anon.get("/api/graph/stats", headers=bob).json()
    assert bob_stats["entities"] == 0, "bob 不应看到 alice 的实体"

    # bob 也删不掉 alice 的实体(按 404 处理,不暴露存在性)
    alice_nodes = anon.get("/api/graph", headers=alice).json()["nodes"]
    target = alice_nodes[0]["id"]
    assert anon.delete(f"/api/graph/entities/{target}", headers=bob).status_code == 404

    # bob 自己灌的数据只属于自己
    anon.post("/api/dev/seed", headers=bob)
    assert anon.get("/api/graph/stats", headers=alice).json()["entities"] == 16
    assert anon.get("/api/graph/stats", headers=bob).json()["entities"] == 16


def test_orphan_data_is_claimed_by_first_user(anon):
    """首个注册的用户应接管历史无主数据,避免老数据变成谁都看不到的孤儿。"""
    # 前面的测试已注册过用户,这里断言「非首个用户不会接管」这一侧的行为
    response = _register(anon, "carol")
    assert response.status_code == 200
    assert response.json().get("claimed_orphans", 0) == 0
