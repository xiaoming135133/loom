from __future__ import annotations

from io import BytesIO

from PIL import Image


def load_sample(client, sample_id: str) -> dict:
    response = client.post("/api/assets", data={"sample_id": sample_id})
    assert response.status_code == 201, response.text
    return response.json()


def align(client, asset_id: str) -> dict:
    response = client.post(f"/api/assets/{asset_id}/align")
    assert response.status_code == 200, response.text
    return response.json()


def test_bootstrap_has_seeded_tree_and_samples(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 12
    assert len(data["samples"]) == 7
    assert data["mode"]["name"] == "离线演示模式"
    assert data["metrics"]["assetCount"] == 0


def test_sample_alignment_covers_threshold_bands(client):
    mount = align(client, load_sample(client, "mount-lhopital")["id"])["suggestion"]
    assert mount["relation"] == "挂载"
    assert mount["targetId"] == "node-lhopital"
    assert mount["thresholdBand"] == "auto"
    assert mount["confidence"] >= 0.80

    extension = align(client, load_sample(client, "extend-equivalent")["id"])["suggestion"]
    assert extension["relation"] == "扩展"
    assert extension["targetId"] == "node-infinitesimal"
    assert extension["thresholdBand"] == "review"
    assert 0.55 <= extension["confidence"] < 0.80

    pending = align(client, load_sample(client, "pending-context-a")["id"])["suggestion"]
    assert pending["relation"] == "待定"
    assert pending["targetId"] is None
    assert pending["thresholdBand"] == "pending"
    assert pending["confidence"] < 0.55


def test_manual_upload_never_fakes_recognition(client):
    image = Image.new("RGB", (320, 200), "#f4f0e5")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    response = client.post(
        "/api/assets",
        files={"file": ("board.png", buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["status"] == "manual_required"
    assert asset["summary"] == ""
    assert asset["latex"] == ""
    assert asset["trace"]["mode"] == "manual-required"

    missing_content = client.post(f"/api/assets/{asset['id']}/align")
    assert missing_content.status_code == 422

    update = client.patch(
        f"/api/assets/{asset['id']}/content",
        json={
            "title": "人工确认的板书",
            "summary": "使用洛必达法则处理零比零型极限",
            "recognized_text": "先检查条件",
            "latex": r"\lim \frac{f}{g}",
        },
    )
    assert update.status_code == 200
    suggestion = align(client, asset["id"])["suggestion"]
    assert suggestion["relation"] == "挂载"
    assert suggestion["targetId"] == "node-lhopital"


def test_decision_writes_revision_and_updates_tree(client):
    asset = load_sample(client, "extend-equivalent")
    suggestion = align(client, asset["id"])["suggestion"]
    assert suggestion["relation"] == "扩展"

    response = client.post(
        f"/api/assets/{asset['id']}/decision",
        json={
            "relation": "挂载",
            "target_id": "node-limit-sequence",
            "time_to_decide_ms": 1450,
        },
    )
    assert response.status_code == 200, response.text
    decided = response.json()["asset"]
    assert decided["status"] == "classified"
    assert decided["node_id"] == "node-limit-sequence"
    assert decided["final_relation"] == "挂载"

    revisions = client.get("/api/revisions").json()["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["time_to_decide_ms"] == 1450
    assert revisions[0]["isCorrection"] is True
    assert client.get("/api/bootstrap").json()["metrics"]["correctedCount"] == 1


def test_pending_batch_confirmation(client):
    assets = [
        load_sample(client, sample_id)
        for sample_id in ("pending-context-a", "pending-context-b", "pending-context-c")
    ]
    for asset in assets:
        suggestion = align(client, asset["id"])["suggestion"]
        assert suggestion["relation"] == "待定"
    assert client.get("/api/bootstrap").json()["metrics"]["pendingCount"] == 3

    response = client.post(
        "/api/pending/batch-decision",
        json={
            "asset_ids": [asset["id"] for asset in assets],
            "relation": "新建",
            "target_id": "chapter-limits",
            "new_node_title": "等价替换的补充结论",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["processed"] == 3
    assert data["pending"] == []

    boot = client.get("/api/bootstrap").json()
    new_node = next(node for node in boot["nodes"] if node["id"] == data["targetId"])
    assert new_node["title"] == "等价替换的补充结论"
    assert new_node["asset_count"] == 3
    assert boot["metrics"]["decisionCount"] == 3


def test_node_move_rejects_cycles_and_records_revision(client):
    response = client.patch(
        "/api/nodes/node-lhopital",
        json={"parent_id": "chapter-series", "time_to_decide_ms": 900},
    )
    assert response.status_code == 200, response.text
    moved = response.json()["node"]
    assert moved["parent_id"] == "chapter-series"

    cycle = client.patch(
        "/api/nodes/chapter-series",
        json={"parent_id": "node-lhopital"},
    )
    assert cycle.status_code == 422
    assert "子树" in cycle.json()["detail"]

    revisions = client.get("/api/revisions").json()["revisions"]
    assert revisions[0]["action"] == "node_move"
    assert revisions[0]["time_to_decide_ms"] == 900


def test_symbol_search_returns_explanations(client):
    align(client, load_sample(client, "symbol-epsilon")["id"])
    response = client.get("/api/search", params={"q": "ε-δ"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert 1 <= len(results) <= 5
    assert any("符号命中" in item["reasons"] for item in results)
    assert any("ε-δ" in item["title"] for item in results)


def test_file_validation_and_missing_asset(client):
    invalid = client.post(
        "/api/assets",
        files={"file": ("board.txt", b"not an image", "text/plain")},
    )
    assert invalid.status_code == 415
    assert client.get("/api/assets/not-found").status_code == 404
    assert client.patch("/api/assets/not-found/content", json={"summary": "x"}).status_code == 404
