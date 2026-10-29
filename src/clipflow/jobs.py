import hashlib
import json
from uuid import uuid4

from .db import Database
from .models import Job, utc_now


def normalized_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, project_id: str, kind: str, input_hash: str) -> Job:
        job_id = str(uuid4())
        with self.db.connection() as conn:
            conn.execute(
                """INSERT INTO jobs (id, project_id, kind, input_hash, status, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?)""",
                (job_id, project_id, kind, input_hash, utc_now()),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(job_id)
        return Job(**{key: row[key] for key in Job.model_fields})

    def get_succeeded(self, project_id: str, kind: str, input_hash: str) -> Job | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """SELECT id FROM jobs
                WHERE project_id = ? AND kind = ? AND input_hash = ? AND status = 'succeeded'
                ORDER BY finished_at DESC LIMIT 1""",
                (project_id, kind, input_hash),
            ).fetchone()
        return self.get(row[0]) if row else None

    def mark_running(self, job_id: str) -> Job:
        return self._update(job_id, "running", started_at=utc_now())

    def mark_succeeded(self, job_id: str, output_path: str) -> Job:
        return self._update(
            job_id, "succeeded", output_path=output_path, finished_at=utc_now()
        )

    def mark_failed(self, job_id: str, code: str, message: str) -> Job:
        return self._update(
            job_id,
            "failed",
            error_code=code,
            error_message=message[:500],
            finished_at=utc_now(),
        )

    def _update(self, job_id: str, status: str, **values: str) -> Job:
        columns = ["status = ?"] + [f"{key} = ?" for key in values]
        params = [status, *values.values(), job_id]
        with self.db.connection() as conn:
            conn.execute(
                f"UPDATE jobs SET {', '.join(columns)} WHERE id = ?", params
            )
        return self.get(job_id)

