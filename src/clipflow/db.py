import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    features_json TEXT NOT NULL,
    price_reference TEXT,
    target_seconds INTEGER NOT NULL CHECK(target_seconds IN (15, 30, 45, 60)),
    platforms_json TEXT NOT NULL,
    review_product_facts INTEGER NOT NULL DEFAULT 0,
    review_asset_rights INTEGER NOT NULL DEFAULT 0,
    review_honest_experience INTEGER NOT NULL DEFAULT 0,
    review_affiliate_disclosure INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK(media_type IN ('image', 'video', 'audio')),
    content_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    rights_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS product_analyses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    output_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_reuse
ON jobs(project_id, kind, input_hash, status);

CREATE TABLE IF NOT EXISTS cost_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_units INTEGER NOT NULL DEFAULT 0,
    output_units INTEGER NOT NULL DEFAULT 0,
    estimated_krw INTEGER NOT NULL DEFAULT 0,
    actual_krw INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_products (
    id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    features_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'partial', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    items_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reel_automation (
    id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    slot INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('fun', 'info', 'promo')),
    status TEXT NOT NULL CHECK(status IN ('queued', 'generating', 'review', 'failed', 'published')),
    project_id TEXT,
    title TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_date, slot)
);
CREATE INDEX IF NOT EXISTS idx_reel_automation_recent
ON reel_automation(run_date DESC, slot DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self._migrate_project_durations()
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    def _migrate_project_durations(self) -> None:
        """기존 DB의 30/45/60초 제약을 15초 릴스까지 안전하게 확장한다."""
        if not self.path.exists():
            return
        conn = sqlite3.connect(self.path)
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()
            if row is None or "15, 30, 45, 60" in (row[0] or ""):
                return
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE projects_new (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    affiliate_url TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    price_reference TEXT,
                    target_seconds INTEGER NOT NULL CHECK(target_seconds IN (15, 30, 45, 60)),
                    platforms_json TEXT NOT NULL,
                    review_product_facts INTEGER NOT NULL DEFAULT 0,
                    review_asset_rights INTEGER NOT NULL DEFAULT 0,
                    review_honest_experience INTEGER NOT NULL DEFAULT 0,
                    review_affiliate_disclosure INTEGER NOT NULL DEFAULT 0,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO projects_new SELECT * FROM projects;
                DROP TABLE projects;
                ALTER TABLE projects_new RENAME TO projects;
                COMMIT;
                """
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
