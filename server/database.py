from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    latex TEXT NOT NULL DEFAULT '',
    node_type TEXT NOT NULL CHECK (node_type IN ('course', 'chapter', 'knowledge')),
    mastery REAL NOT NULL DEFAULT 0.0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    source_asset_id TEXT,
    auto_created INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_path TEXT,
    mime_type TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('sample', 'upload')),
    sample_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    recognized_text TEXT NOT NULL DEFAULT '',
    latex TEXT NOT NULL DEFAULT '',
    regions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN (
            'processing',
            'classified',
            'needs_review',
            'pending',
            'manual_required',
            'recognition_failed'
        )
    ),
    node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    suggested_relation TEXT,
    suggested_target_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    final_relation TEXT,
    final_target_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    confidence REAL,
    reason TEXT,
    threshold_band TEXT,
    trace_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    suggested_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    time_to_decide_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_node ON assets(node_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_revisions_asset ON revisions(asset_id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
