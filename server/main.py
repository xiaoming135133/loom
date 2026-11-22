from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

try:
    from .database import Database
    from .demo_data import CROSS_EDGES, NODES, SAMPLES
    from .engine import build_paths, search_content
    from .providers import (
        OfflineRecognitionProvider,
        OfflineRelationJudge,
        RecognitionProvider,
        RelationJudge,
    )
except ImportError:  # 支持在 server/ 目录直接运行 uvicorn main:app
    from database import Database
    from demo_data import CROSS_EDGES, NODES, SAMPLES
    from engine import build_paths, search_content
    from providers import (
        OfflineRecognitionProvider,
        OfflineRelationJudge,
        RecognitionProvider,
        RelationJudge,
    )


Image.MAX_IMAGE_PIXELS = 40_000_000
REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
MAX_TITLE = 80
MAX_SUMMARY = 1200
MAX_TEXT = 12_000
MAX_LATEX = 6000


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int

    @property
    def database_path(self) -> Path:
        return self.data_dir / "loom.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def settings_from_environment(data_dir: str | Path | None = None) -> Settings:
    configured = Path(
        data_dir
        or os.getenv("LOOM_DATA_DIR")
        or REPO_ROOT / "server" / "data"
    )
    if not configured.is_absolute():
        configured = (REPO_ROOT / configured).resolve()
    max_bytes = int(os.getenv("LOOM_MAX_UPLOAD_BYTES", "8388608"))
    return Settings(configured, max_bytes)


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY)
    recognized_text: str | None = Field(default=None, max_length=MAX_TEXT)
    latex: str | None = Field(default=None, max_length=MAX_LATEX)


class DecisionRequest(BaseModel):
    relation: str
    target_id: str | None = None
    new_node_title: str | None = Field(default=None, max_length=MAX_TITLE)
    time_to_decide_ms: int = Field(default=0, ge=0, le=86_400_000)


class BatchDecisionRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=5)
    relation: str
    target_id: str | None = None
    new_node_title: str | None = Field(default=None, max_length=MAX_TITLE)
    time_to_decide_ms: int = Field(default=0, ge=0, le=86_400_000)


class NodeUpdate(BaseModel):
    parent_id: str | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY)
    time_to_decide_ms: int = Field(default=0, ge=0, le=86_400_000)


def row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    node = dict(row)
    node["auto_created"] = bool(node["auto_created"])
    return node


def safe_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def serialize_asset(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    asset = dict(row)
    stored_path = asset.pop("stored_path", None)
    if stored_path:
        original_url = f"/assets/demo/{Path(stored_path).name}" if asset["source_kind"] == "sample" else f"/uploads/{Path(stored_path).name}"
    else:
        original_url = None
    asset["original_url"] = original_url
    asset["regions"] = safe_json(asset.pop("regions_json", "[]"), [])
    asset["trace"] = safe_json(asset.pop("trace_json", "{}"), {})
    return asset


def validate_text(value: str, limit: int, label: str) -> None:
    if len(value) > limit:
        raise HTTPException(status_code=422, detail=f"{label}超过 {limit} 个字符")


def fetch_node(conn: sqlite3.Connection, node_id: str | None) -> sqlite3.Row | None:
    if not node_id:
        return None
    return conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()


def list_nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT n.*, COUNT(a.id) AS asset_count
        FROM nodes n
        LEFT JOIN assets a ON a.node_id = n.id
        GROUP BY n.id
        ORDER BY n.sort_order, n.title
        """
    ).fetchall()
    return [row_to_node(row) for row in rows]


def list_assets(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT a.*, n.title AS node_title
        FROM assets a
        LEFT JOIN nodes n ON n.id = a.node_id
        ORDER BY a.created_at DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [serialize_asset(row) for row in conn.execute(sql, params).fetchall()]


def fetch_asset(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT a.*, n.title AS node_title
        FROM assets a
        LEFT JOIN nodes n ON n.id = a.node_id
        WHERE a.id = ?
        """,
        (asset_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="素材不存在")
    return serialize_asset(row)


def seed_if_needed(conn: sqlite3.Connection) -> None:
    now = utcnow()
    if conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0:
        for node in NODES:
            conn.execute(
                """
                INSERT INTO nodes (
                    id, parent_id, title, summary, content, latex, node_type,
                    mastery, sort_order, source_asset_id, auto_created,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)
                """,
                (
                    node["id"],
                    node["parent_id"],
                    node["title"],
                    node["summary"],
                    node["content"],
                    node["latex"],
                    node["node_type"],
                    0.35 if node["node_type"] == "knowledge" else 0.0,
                    node["sort_order"],
                    now,
                    now,
                ),
            )
    if conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0:
        for edge in CROSS_EDGES:
            conn.execute(
                """
                INSERT INTO edges (
                    id, source_id, target_id, relation, reason, confidence,
                    asset_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    edge["id"],
                    edge["source_id"],
                    edge["target_id"],
                    edge["relation"],
                    edge["reason"],
                    edge["confidence"],
                    now,
                ),
            )


def _insert_node(
    conn: sqlite3.Connection,
    *,
    parent_id: str | None,
    title: str,
    summary: str,
    content: str,
    latex: str,
    source_asset_id: str,
    auto_created: bool,
) -> str:
    node_id = new_id("node")
    now = utcnow()
    sort_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM nodes WHERE parent_id IS ?",
        (parent_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO nodes (
            id, parent_id, title, summary, content, latex, node_type,
            mastery, sort_order, source_asset_id, auto_created,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'knowledge', 0, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            parent_id,
            title[:MAX_TITLE],
            summary[:MAX_SUMMARY],
            content[:MAX_TEXT],
            latex[:MAX_LATEX],
            sort_order,
            source_asset_id,
            int(auto_created),
            now,
            now,
        ),
    )
    return node_id


def _remove_previous_auto_node(conn: sqlite3.Connection, asset: sqlite3.Row) -> None:
    auto_nodes = conn.execute(
        "SELECT id FROM nodes WHERE source_asset_id = ? AND auto_created = 1",
        (asset["id"],),
    ).fetchall()
    for row in auto_nodes:
        node_id = row["id"]
        linked = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE node_id = ? AND id != ?",
            (node_id, asset["id"]),
        ).fetchone()[0]
        children = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE parent_id = ?",
            (node_id,),
        ).fetchone()[0]
        if linked == 0 and children == 0:
            conn.execute("UPDATE assets SET node_id = NULL WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))


def _apply_direct_assignment(
    conn: sqlite3.Connection,
    asset_id: str,
    relation: str,
    target_id: str | None,
    *,
    status: str,
) -> str | None:
    conn.execute(
        """
        UPDATE assets
        SET node_id = ?, final_relation = ?, final_target_id = ?,
            status = ?, updated_at = ?
        WHERE id = ?
        """,
        (target_id, relation, target_id, status, utcnow(), asset_id),
    )
    return target_id


def _validate_target(conn: sqlite3.Connection, target_id: str | None, required: bool = True) -> sqlite3.Row | None:
    if not target_id:
        if required:
            raise HTTPException(status_code=422, detail="该关系需要目标节点")
        return None
    target = fetch_node(conn, target_id)
    if not target:
        raise HTTPException(status_code=422, detail="目标节点不存在")
    return target


def _record_revision(
    conn: sqlite3.Connection,
    *,
    asset_id: str | None,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    suggested: dict[str, Any],
    confidence: float | None,
    elapsed_ms: int,
) -> str:
    revision_id = new_id("rev")
    conn.execute(
        """
        INSERT INTO revisions (
            id, asset_id, action, before_json, after_json, suggested_json,
            confidence, time_to_decide_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            asset_id,
            action,
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            json.dumps(suggested, ensure_ascii=False),
            confidence,
            elapsed_ms,
            utcnow(),
        ),
    )
    return revision_id


def _revision_correction(revision: dict[str, Any]) -> bool:
    if revision["action"] not in {"asset_decision", "batch_asset_decision"}:
        return False
    suggested = revision.get("suggested") or {}
    after = revision.get("after") or {}
    return (
        suggested.get("relation") != after.get("relation")
        or suggested.get("target_id") != after.get("target_id")
    )


def metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    pending_count = conn.execute("SELECT COUNT(*) FROM assets WHERE status = 'pending'").fetchone()[0]
    rows = conn.execute("SELECT * FROM revisions ORDER BY created_at DESC").fetchall()
    revisions = []
    for row in rows:
        item = dict(row)
        item["before"] = safe_json(item.pop("before_json", "{}"), {})
        item["after"] = safe_json(item.pop("after_json", "{}"), {})
        item["suggested"] = safe_json(item.pop("suggested_json", "{}"), {})
        revisions.append(item)
    decisions = [item for item in revisions if item["action"] in {"asset_decision", "batch_asset_decision"}]
    corrections = [item for item in decisions if _revision_correction(item)]
    return {
        "assetCount": asset_count,
        "nodeCount": node_count,
        "pendingCount": pending_count,
        "revisionCount": len(revisions),
        "decisionCount": len(decisions),
        "correctedCount": len(corrections),
        "correctionRate": round(len(corrections) / len(decisions), 3) if decisions else 0.0,
    }


def create_app(
    data_dir: str | Path | None = None,
    *,
    recognizer: RecognitionProvider | None = None,
    relation_judge: RelationJudge | None = None,
) -> FastAPI:
    settings = settings_from_environment(data_dir)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_path)
    active_recognizer = recognizer or OfflineRecognitionProvider()
    active_judge = relation_judge or OfflineRelationJudge()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        with database.connection() as conn:
            seed_if_needed(conn)
        yield

    app = FastAPI(
        title="LOOM 结构服务",
        version="0.1.0",
        description="离线可演示的增量结构对齐网页 MVP。",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.recognizer = active_recognizer
    app.state.relation_judge = active_judge

    if (WEB_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "offline-demo",
            "database": str(settings.database_path),
            "time": utcnow(),
        }

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        with database.connection() as conn:
            nodes = list_nodes(conn)
            assets = list_assets(conn, limit=30)
            pending = [asset for asset in list_assets(conn) if asset["status"] == "pending"]
            paths = build_paths(nodes)
            for node in nodes:
                node["path"] = paths[node["id"]]
            return {
                "nodes": nodes,
                "assets": assets,
                "pending": pending,
                "metrics": metrics(conn),
                "samples": [
                    {
                        "id": sample["id"],
                        "title": sample["title"],
                        "eyebrow": sample["eyebrow"],
                        "summary": sample["summary"],
                        "original_url": f"/assets/demo/{sample['file']}",
                    }
                    for sample in SAMPLES
                ],
                "mode": {
                    "name": "离线演示模式",
                    "detail": "使用内置高数样张与确定性规则；不调用外部 OCR 或大模型。",
                },
            }

    @app.post("/api/assets", status_code=201)
    async def create_asset(
        sample_id: str | None = Form(default=None),
        file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        if sample_id:
            sample = next((item for item in SAMPLES if item["id"] == sample_id), None)
            if not sample:
                raise HTTPException(status_code=404, detail="演示样张不存在")
            recognition = active_recognizer.recognize_sample(sample)
            asset_id = new_id("asset")
            now = utcnow()
            with database.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO assets (
                        id, title, original_filename, stored_path, mime_type,
                        source_kind, sample_id, summary, recognized_text, latex,
                        regions_json, status, trace_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'image/png', 'sample', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        sample["title"],
                        f"sample-{sample['file']}",
                        sample["file"],
                        sample["id"],
                        recognition.summary,
                        recognition.recognized_text,
                        recognition.latex,
                        json.dumps(recognition.regions, ensure_ascii=False),
                        recognition.status,
                        json.dumps(recognition.trace, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                return fetch_asset(conn, asset_id)

        if not file:
            raise HTTPException(status_code=422, detail="请选择图片或演示样张")
        raw = await file.read(settings.max_upload_bytes + 1)
        if not raw:
            raise HTTPException(status_code=422, detail="图片为空")
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="图片不能超过 8 MB")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 40_000_000:
                    raise HTTPException(status_code=413, detail="图片像素尺寸过大")
                image_format = (image.format or "").upper()
        except (UnidentifiedImageError, OSError):
            raise HTTPException(status_code=415, detail="文件不是可识别的图片") from None
        extension_map = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
        extension = extension_map.get(image_format)
        if not extension:
            raise HTTPException(status_code=415, detail="仅支持 PNG、JPEG 或 WebP")
        upload_name = f"{uuid.uuid4().hex}{extension}"
        (settings.uploads_dir / upload_name).write_bytes(raw)
        original_name = Path(file.filename or f"upload{extension}").name[:180]
        title = Path(original_name).stem[:MAX_TITLE] or "新板书"
        recognition = active_recognizer.recognize_upload(title, original_name)
        asset_id = new_id("asset")
        now = utcnow()
        with database.connection() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    id, title, original_filename, stored_path, mime_type,
                    source_kind, sample_id, summary, recognized_text, latex,
                    regions_json, status, trace_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'upload', NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    title,
                    original_name,
                    upload_name,
                    file.content_type or f"image/{image_format.lower()}",
                    recognition.summary,
                    recognition.recognized_text,
                    recognition.latex,
                    json.dumps(recognition.regions, ensure_ascii=False),
                    recognition.status,
                    json.dumps(recognition.trace, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return fetch_asset(conn, asset_id)

    @app.patch("/api/assets/{asset_id}/content")
    def update_content(asset_id: str, payload: ContentUpdate) -> dict[str, Any]:
        updates = payload.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=422, detail="没有可保存的修改")
        for key, limit, label in (
            ("title", MAX_TITLE, "标题"),
            ("summary", MAX_SUMMARY, "摘要"),
            ("recognized_text", MAX_TEXT, "识别文本"),
            ("latex", MAX_LATEX, "LaTeX"),
        ):
            if key in updates:
                validate_text(updates[key], limit, label)
        with database.connection() as conn:
            fetch_asset(conn, asset_id)
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE assets SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), utcnow(), asset_id),
            )
            return fetch_asset(conn, asset_id)

    @app.post("/api/assets/{asset_id}/align")
    def align_asset(asset_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        with database.connection() as conn:
            asset_row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not asset_row:
                raise HTTPException(status_code=404, detail="素材不存在")
            if not (asset_row["summary"] or asset_row["recognized_text"] or asset_row["latex"]):
                raise HTTPException(status_code=422, detail="请先补充摘要、文字或 LaTeX")
            nodes = list_nodes(conn)
            suggestion = active_judge.judge(dict(asset_row), nodes)
            _remove_previous_auto_node(conn, asset_row)

            chosen_target = suggestion["targetId"]
            if suggestion["relation"] == "待定":
                final_node_id = None
                status = "pending"
            else:
                if suggestion["relation"] in {"新建", "扩展"}:
                    final_node_id = _insert_node(
                        conn,
                        parent_id=suggestion["targetId"],
                        title=str(asset_row["title"]),
                        summary=str(asset_row["summary"]),
                        content=str(asset_row["recognized_text"]),
                        latex=str(asset_row["latex"]),
                        source_asset_id=asset_id,
                        auto_created=True,
                    )
                    chosen_target = final_node_id
                else:
                    _validate_target(conn, chosen_target)
                    final_node_id = chosen_target
                status = "classified" if suggestion["thresholdBand"] == "auto" else "needs_review"

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            trace = safe_json(asset_row["trace_json"], {})
            trace.update(
                {
                    "provider": trace.get("provider", "offline"),
                    "alignmentMs": elapsed_ms,
                    "thresholdBand": suggestion["thresholdBand"],
                }
            )
            conn.execute(
                """
                UPDATE assets
                SET status = ?, node_id = ?, suggested_relation = ?,
                    suggested_target_id = ?, final_relation = ?,
                    final_target_id = ?, confidence = ?, reason = ?,
                    threshold_band = ?, trace_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    final_node_id,
                    suggestion["relation"],
                    suggestion["targetId"],
                    suggestion["relation"],
                    chosen_target,
                    suggestion["confidence"],
                    suggestion["reason"],
                    suggestion["thresholdBand"],
                    json.dumps(trace, ensure_ascii=False),
                    utcnow(),
                    asset_id,
                ),
            )
            return {
                "asset": fetch_asset(conn, asset_id),
                "suggestion": {
                    **suggestion,
                    "autoApplied": suggestion["thresholdBand"] in {"auto", "review"},
                    "finalTargetId": chosen_target,
                },
            }

    @app.post("/api/assets/{asset_id}/decision")
    def decide_asset(asset_id: str, payload: DecisionRequest) -> dict[str, Any]:
        if payload.relation not in {"挂载", "扩展", "新建", "待定"}:
            raise HTTPException(status_code=422, detail="未知的关系类型")
        with database.connection() as conn:
            asset_row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not asset_row:
                raise HTTPException(status_code=404, detail="素材不存在")
            if not asset_row["suggested_relation"]:
                raise HTTPException(status_code=409, detail="请先运行结构对齐")
            _remove_previous_auto_node(conn, asset_row)
            before = {
                "relation": asset_row["suggested_relation"],
                "target_id": asset_row["suggested_target_id"],
            }
            suggestion = {
                **before,
                "confidence": asset_row["confidence"],
                "reason": asset_row["reason"],
            }
            chosen_target = payload.target_id
            if payload.relation == "待定":
                final_node_id = None
                status = "pending"
                chosen_target = None
            elif payload.relation in {"挂载", "扩展", "新建"}:
                if payload.relation == "新建":
                    parent = _validate_target(conn, payload.target_id, required=False)
                    title = (payload.new_node_title or asset_row["title"]).strip()
                    if not title:
                        raise HTTPException(status_code=422, detail="新知识点需要标题")
                    final_node_id = _insert_node(
                        conn,
                        parent_id=parent["id"] if parent else None,
                        title=title,
                        summary=str(asset_row["summary"]),
                        content=str(asset_row["recognized_text"]),
                        latex=str(asset_row["latex"]),
                        source_asset_id=asset_id,
                        auto_created=False,
                    )
                    chosen_target = final_node_id
                elif payload.relation == "扩展":
                    parent = _validate_target(conn, payload.target_id)
                    final_node_id = _insert_node(
                        conn,
                        parent_id=parent["id"],
                        title=(payload.new_node_title or asset_row["title"]).strip(),
                        summary=str(asset_row["summary"]),
                        content=str(asset_row["recognized_text"]),
                        latex=str(asset_row["latex"]),
                        source_asset_id=asset_id,
                        auto_created=False,
                    )
                    chosen_target = final_node_id
                else:
                    _validate_target(conn, payload.target_id)
                    final_node_id = payload.target_id
                status = "classified"
            else:
                raise HTTPException(status_code=422, detail="未知的关系类型")

            after = {"relation": payload.relation, "target_id": chosen_target}
            conn.execute(
                """
                UPDATE assets
                SET status = ?, node_id = ?, final_relation = ?,
                    final_target_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, final_node_id, payload.relation, chosen_target, utcnow(), asset_id),
            )
            _record_revision(
                conn,
                asset_id=asset_id,
                action="asset_decision",
                before=before,
                after=after,
                suggested=suggestion,
                confidence=asset_row["confidence"],
                elapsed_ms=payload.time_to_decide_ms,
            )
            return {"asset": fetch_asset(conn, asset_id), "revisionRecorded": True}

    @app.post("/api/pending/batch-decision")
    def decide_batch(payload: BatchDecisionRequest) -> dict[str, Any]:
        if payload.relation not in {"挂载", "扩展", "新建"}:
            raise HTTPException(status_code=422, detail="批量确认只支持挂载、扩展或新建")
        unique_ids = list(dict.fromkeys(payload.asset_ids))
        with database.connection() as conn:
            placeholders = ",".join("?" for _ in unique_ids)
            rows = conn.execute(
                f"SELECT * FROM assets WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            if len(rows) != len(unique_ids):
                raise HTTPException(status_code=404, detail="部分素材不存在")

            shared_target: str | None = None
            if payload.relation == "新建":
                parent = _validate_target(conn, payload.target_id, required=False)
                title = (payload.new_node_title or "批量确认的新知识点").strip()
                shared_target = _insert_node(
                    conn,
                    parent_id=parent["id"] if parent else None,
                    title=title,
                    summary="由未归类箱批量确认生成。",
                    content="",
                    latex="",
                    source_asset_id=unique_ids[0],
                    auto_created=False,
                )
            elif payload.relation == "扩展":
                parent = _validate_target(conn, payload.target_id)
                title = (payload.new_node_title or "批量确认的扩展内容").strip()
                shared_target = _insert_node(
                    conn,
                    parent_id=parent["id"],
                    title=title,
                    summary="由未归类箱批量确认生成的扩展节点。",
                    content="",
                    latex="",
                    source_asset_id=unique_ids[0],
                    auto_created=False,
                )
            else:
                target = _validate_target(conn, payload.target_id)
                shared_target = target["id"]

            for row in rows:
                _remove_previous_auto_node(conn, row)
                suggested = {
                    "relation": row["suggested_relation"],
                    "target_id": row["suggested_target_id"],
                    "confidence": row["confidence"],
                    "reason": row["reason"],
                }
                before = {
                    "relation": row["final_relation"] or row["suggested_relation"] or "待定",
                    "target_id": row["final_target_id"] or row["node_id"],
                }
                conn.execute(
                    """
                    UPDATE assets
                    SET status = 'classified', node_id = ?, final_relation = ?,
                        final_target_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (shared_target, payload.relation, shared_target, utcnow(), row["id"]),
                )
                _record_revision(
                    conn,
                    asset_id=row["id"],
                    action="batch_asset_decision",
                    before=before,
                    after={"relation": payload.relation, "target_id": shared_target},
                    suggested=suggested,
                    confidence=row["confidence"],
                    elapsed_ms=payload.time_to_decide_ms,
                )
            return {
                "processed": len(rows),
                "targetId": shared_target,
                "pending": [asset for asset in list_assets(conn) if asset["status"] == "pending"],
            }

    @app.patch("/api/nodes/{node_id}")
    def update_node(node_id: str, payload: NodeUpdate) -> dict[str, Any]:
        with database.connection() as conn:
            node = fetch_node(conn, node_id)
            if not node:
                raise HTTPException(status_code=404, detail="节点不存在")
            if node["node_type"] == "course" and payload.parent_id is not None:
                raise HTTPException(status_code=422, detail="课程根节点不能移动")
            if payload.parent_id == node_id:
                raise HTTPException(status_code=422, detail="节点不能挂到自己下面")
            if payload.parent_id is not None:
                target = fetch_node(conn, payload.parent_id)
                if not target:
                    raise HTTPException(status_code=422, detail="目标节点不存在")
                descendants = {
                    row["id"]
                    for row in conn.execute(
                        """
                        WITH RECURSIVE tree(id) AS (
                            SELECT id FROM nodes WHERE parent_id = ?
                            UNION ALL
                            SELECT n.id FROM nodes n JOIN tree t ON n.parent_id = t.id
                        )
                        SELECT id FROM tree
                        """,
                        (node_id,),
                    ).fetchall()
                }
                if payload.parent_id in descendants:
                    raise HTTPException(status_code=422, detail="不能把节点移动到自己的子树中")
            updates: dict[str, Any] = {}
            if "parent_id" in payload.model_fields_set:
                updates["parent_id"] = payload.parent_id
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise HTTPException(status_code=422, detail="节点标题不能为空")
                validate_text(title, MAX_TITLE, "节点标题")
                updates["title"] = title
            if payload.summary is not None:
                validate_text(payload.summary, MAX_SUMMARY, "节点摘要")
                updates["summary"] = payload.summary
            if not updates:
                raise HTTPException(status_code=422, detail="没有可保存的修改")
            before = {key: node[key] for key in updates}
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE nodes SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), utcnow(), node_id),
            )
            linked_asset = conn.execute(
                "SELECT id FROM assets WHERE node_id = ? ORDER BY created_at LIMIT 1",
                (node_id,),
            ).fetchone()
            _record_revision(
                conn,
                asset_id=linked_asset["id"] if linked_asset else None,
                action="node_move" if payload.parent_id is not None else "node_edit",
                before=before,
                after=updates,
                suggested={},
                confidence=None,
                elapsed_ms=payload.time_to_decide_ms,
            )
            return {"node": row_to_node(fetch_node(conn, node_id))}

    @app.get("/api/search")
    def search(q: str = Query(default="", max_length=80)) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"query": query, "results": []}
        with database.connection() as conn:
            nodes = list_nodes(conn)
            assets = list_assets(conn)
            return {"query": query, "results": search_content(query, nodes, assets, limit=5)}

    @app.get("/api/graph")
    def graph() -> dict[str, Any]:
        with database.connection() as conn:
            nodes = list_nodes(conn)
            edges = [dict(row) for row in conn.execute("SELECT * FROM edges ORDER BY created_at").fetchall()]
            paths = build_paths(nodes)
            for node in nodes:
                node["path"] = paths[node["id"]]
            return {
                "nodes": nodes,
                "parentEdges": [
                    {"source": node["parent_id"], "target": node["id"]}
                    for node in nodes
                    if node["parent_id"]
                ],
                "crossEdges": edges,
            }

    @app.get("/api/revisions")
    def revisions(limit: int = Query(default=40, ge=1, le=200)) -> dict[str, Any]:
        with database.connection() as conn:
            rows = conn.execute(
                """
                SELECT r.*, a.title AS asset_title
                FROM revisions r
                LEFT JOIN assets a ON a.id = r.asset_id
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["before"] = safe_json(item.pop("before_json", "{}"), {})
                item["after"] = safe_json(item.pop("after_json", "{}"), {})
                item["suggested"] = safe_json(item.pop("suggested_json", "{}"), {})
                item["isCorrection"] = _revision_correction(item)
                result.append(item)
            return {"revisions": result}

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        index_file = WEB_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=503, detail="网页资源尚未安装")
        return FileResponse(index_file)

    return app


app = create_app()
