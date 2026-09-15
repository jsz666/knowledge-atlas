"""核心链路自动化测试。

覆盖:健康检查、示例数据、统计、节点详情、撤销 / 重做栈、批量撤销、
导出与导入闭环、实体消歧合并、问答与历史、最短路径。

用独立的临时 SQLite 库运行,不会污染 data/knowledge_atlas.db。
运行方式(在 backend 目录):
    python3 -m pytest tests -q
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# 临时数据库由 tests/conftest.py 统一指定(必须在导入 app 之前)
from app.main import app  # noqa: E402
from app.database import Base, engine  # noqa: E402

Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="module")
def client():
    """带登录态的客户端:写入接口要求登录,且数据按账号隔离。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/auth/register", json={"username": "tester", "password": "test12345"}
        )
        assert response.status_code == 200, response.text
        test_client.headers.update(
            {"Authorization": f"Bearer {response.json()['token']}"}
        )
        yield test_client


def reset_and_seed(client):
    client.post("/api/dev/reset")
    result = client.post("/api/dev/seed").json()
    assert result["seeded"] is True


def stats(client):
    return client.get("/api/graph/stats").json()


def entity_id(client, name):
    """按名字取实体 id,避免依赖自增主键(多个测试文件共库时 id 会变化)。"""
    for node in client.get("/api/graph").json()["nodes"]:
        if node["name"] == name:
            return node["id"]
    raise AssertionError(f"图谱中找不到实体:{name}")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "model_available" in body


def test_seed_and_graph(client):
    reset_and_seed(client)
    graph = client.get("/api/graph").json()
    assert len(graph["nodes"]) == 16
    assert len(graph["edges"]) >= 18


def test_stats_counts_cross_doc_entities(client):
    body = stats(client)
    assert body["documents"] == 4
    assert body["entities"] == 16
    # seed 里让 Transformer 同时出现在两篇文档中
    assert body["cross_doc_entities"] >= 1


def test_entity_detail_exposes_evidence(client):
    target = entity_id(client, "Transformer")
    detail = client.get(f"/api/graph/entities/{target}").json()
    assert detail["entity"]["name"] == "Transformer"
    assert detail["neighbors"]
    assert len(detail["evidence"]) >= 1


def test_undo_and_redo(client):
    reset_and_seed(client)

    target = entity_id(client, "RAG")
    deleted = client.delete(f"/api/graph/entities/{target}").json()
    snapshot_id = deleted["snapshot_id"]
    assert stats(client)["entities"] == 15

    restored = client.post("/api/graph/undo").json()
    assert restored["restored"]["kind"] == "entity"
    assert restored["restored"]["id"] == target
    assert stats(client)["entities"] == 16

    redone = client.post("/api/graph/redo").json()
    assert redone["redone"]["id"] == target
    assert stats(client)["entities"] == 15

    # 指定快照撤销
    client.post(f"/api/graph/undo/{snapshot_id}")
    assert stats(client)["entities"] == 16


def test_batch_undo(client):
    reset_and_seed(client)
    ids = [
        client.delete(f"/api/graph/entities/{entity_id(client, 'BERT')}").json()["snapshot_id"],
        client.delete(f"/api/graph/entities/{entity_id(client, '知识图谱')}").json()[
            "snapshot_id"
        ],
    ]
    assert len(client.get("/api/graph/trash").json()["undo"]) >= 2

    result = client.post("/api/graph/trash/undo-batch", json={"ids": ids}).json()
    assert len(result["restored"]) == 2
    assert stats(client)["entities"] == 16

    # 重复撤销无效 id 应 404
    assert client.post("/api/graph/trash/undo-batch", json={"ids": [999999]}).status_code == 404


def test_export_import_roundtrip(client):
    reset_and_seed(client)

    snapshot = client.get("/api/export/json").json()
    assert snapshot["stats"]["entities"] == 16
    assert snapshot["chunks"], "快照应包含原文片段,否则导入无法还原"

    payload = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    files = {"file": ("snapshot.json", payload, "application/json")}

    client.post("/api/dev/reset")
    assert stats(client)["entities"] == 0

    imported = client.post("/api/documents/import", files=files).json()
    assert imported["imported"]["entities"] == 16
    assert stats(client)["entities"] == 16

    # 再次导入应幂等(不产生重复数据)
    again = client.post("/api/documents/import", files=files).json()
    assert again["imported"]["entities"] == 0
    assert stats(client)["entities"] == 16


def test_export_formats(client):
    csv_relations = client.get("/api/export/csv?kind=relations")
    assert csv_relations.status_code == 200
    assert "relation_type" in csv_relations.text

    csv_entities = client.get("/api/export/csv?kind=entities")
    assert "name,type" in csv_entities.text

    markdown = client.get("/api/export/markdown")
    assert "# 知识星图导出" in markdown.text

    assert client.get("/api/export/csv?kind=bogus").status_code == 400


def test_dedupe_and_merge(client):
    reset_and_seed(client)

    variant = client.post("/api/graph/entities", json={"name": "transformer", "type": "method"}).json()
    groups = client.get("/api/graph/duplicates").json()["groups"]
    assert groups, "归一化后应识别为重复实体"

    keep = next(item["id"] for item in groups[0]["items"] if item["name"] == "Transformer")
    merged = client.post("/api/graph/merge", json={"keep_id": keep, "merge_ids": [variant["id"]]}).json()
    assert merged["merged"]["merged_entities"] == 1
    assert client.get("/api/graph/duplicates").json()["groups"] == []

    assert client.post("/api/graph/merge", json={"keep_id": keep, "merge_ids": []}).status_code == 400
    assert client.post("/api/graph/merge", json={"keep_id": 999999, "merge_ids": [keep]}).status_code == 404


def test_qa_ask_and_history(client):
    reset_and_seed(client)

    asked = client.post(
        "/api/qa/ask",
        json={"question": "RAG 和 知识图谱 有什么关系?", "max_hops": 2},
    ).json()
    assert asked["answer"]
    assert asked["evidence"], "应返回原文证据"

    records = client.get("/api/qa/records").json()
    assert records

    detail = client.get(f"/api/qa/records/{records[0]['id']}").json()
    assert detail["paths"], "历史详情应带关系路径"
    assert client.get("/api/qa/records/999999").status_code == 404


def test_shortest_path(client):
    reset_and_seed(client)
    source = entity_id(client, "BERT")
    target = entity_id(client, "幻觉")
    path = client.get(f"/api/graph/path?source_id={source}&target_id={target}").json()
    assert path["found"] is True
    assert path["path"][0] == "BERT"
    assert path["path"][-1] == "幻觉"


def test_document_chunks(client):
    reset_and_seed(client)
    documents = client.get("/api/documents").json()
    assert documents
    chunks = client.get(f"/api/documents/{documents[0]['id']}/chunks").json()
    assert chunks["chunks"]
    assert client.get("/api/documents/999999/chunks").status_code == 404


def test_delete_document_cascades_chunks_and_evidence(client):
    """删除文档应级联清掉片段与证据,不留下孤儿数据。"""
    reset_and_seed(client)

    from app.database import SessionLocal
    from app.models import Chunk, Evidence

    doc_id = client.get("/api/documents").json()[0]["id"]

    db = SessionLocal()
    try:
        assert db.query(Chunk).filter(Chunk.document_id == doc_id).count() > 0
        assert db.query(Evidence).filter(Evidence.document_id == doc_id).count() > 0
    finally:
        db.close()

    assert client.delete(f"/api/documents/{doc_id}").status_code == 200

    db = SessionLocal()
    try:
        assert db.query(Chunk).filter(Chunk.document_id == doc_id).count() == 0
        assert db.query(Evidence).filter(Evidence.document_id == doc_id).count() == 0
    finally:
        db.close()


def test_mention_count_matches_evidence(client):
    """实体热度应等于支撑它的证据条数。"""
    reset_and_seed(client)

    from app.database import SessionLocal
    from app.models import Entity, Evidence

    db = SessionLocal()
    try:
        # 同名实体在不同账号下各有一份,按 id 取当前账号的那个
        entity = db.get(Entity, entity_id(client, "Transformer"))
        expected = (
            db.query(Evidence)
            .filter(Evidence.source_type == "entity", Evidence.source_id == entity.id)
            .count()
        )
        assert expected > 0
        assert entity.mention_count == expected
    finally:
        db.close()


def test_merge_records_alias_and_cascades_on_delete(client):
    """合并后旧名字记为别名仍可搜到;删除实体时别名被级联清理。"""
    reset_and_seed(client)

    from app.database import SessionLocal
    from app.models import EntityAlias

    variant = client.post(
        "/api/graph/entities", json={"name": "transformer", "type": "method"}
    ).json()
    groups = client.get("/api/graph/duplicates").json()["groups"]
    keep = next(item["id"] for item in groups[0]["items"] if item["name"] == "Transformer")

    client.post("/api/graph/merge", json={"keep_id": keep, "merge_ids": [variant["id"]]})

    db = SessionLocal()
    try:
        aliases = {
            row.alias for row in db.query(EntityAlias).filter(EntityAlias.entity_id == keep)
        }
        assert "transformer" in aliases, "被合并掉的旧名字应保留为别名"
    finally:
        db.close()

    # 用旧名字搜索仍应命中保留下来的实体
    found = client.get("/api/graph/search?q=transformer").json()
    assert any(node["id"] == keep for node in found["nodes"])

    client.delete(f"/api/graph/entities/{keep}")
    db = SessionLocal()
    try:
        assert db.query(EntityAlias).filter(EntityAlias.entity_id == keep).count() == 0
    finally:
        db.close()
