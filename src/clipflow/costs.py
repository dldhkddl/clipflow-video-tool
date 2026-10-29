from uuid import uuid4

from .db import Database
from .models import utc_now


class MonthlyCostLimitExceeded(RuntimeError):
    pass


class CostGuard:
    def __init__(self, db: Database, warning_krw: int, limit_krw: int):
        self.db = db
        self.warning_krw = warning_krw
        self.limit_krw = limit_krw

    def month_total(self) -> int:
        month = utc_now()[:7]
        with self.db.connection() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(actual_krw), 0)
                FROM cost_entries WHERE substr(created_at, 1, 7) = ?""",
                (month,),
            ).fetchone()
        return int(row[0])

    def authorize(self, estimated_krw: int, explicit_override: bool = False) -> None:
        if estimated_krw < 0:
            raise ValueError("예상 비용은 음수일 수 없습니다.")
        projected = self.month_total() + estimated_krw
        if projected > self.limit_krw and not explicit_override:
            raise MonthlyCostLimitExceeded(
                f"월 비용 한도 {self.limit_krw:,}원을 초과합니다."
            )

    def record(
        self,
        actual_krw: int,
        *,
        kind: str,
        provider: str,
        model: str,
        project_id: str | None = None,
        job_id: str | None = None,
        input_units: int = 0,
        output_units: int = 0,
        estimated_krw: int = 0,
    ) -> None:
        with self.db.connection() as conn:
            conn.execute(
                """INSERT INTO cost_entries (
                    id, project_id, job_id, provider, model, kind,
                    input_units, output_units, estimated_krw, actual_krw, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), project_id, job_id, provider, model, kind,
                    input_units, output_units, estimated_krw, actual_krw, utc_now(),
                ),
            )

