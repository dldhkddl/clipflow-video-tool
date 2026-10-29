from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
import msvcrt
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl

from .db import Database
from .models import ProjectCreate, utc_now
from .workflow import AutoBuildOptions, run_auto_build


class DailyProductCreate(BaseModel):
    product_name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    affiliate_url: HttpUrl
    features: list[str] = Field(min_length=1, max_length=12)


class DailyProduct(DailyProductCreate):
    id: str
    enabled: bool
    position: int
    last_run_at: str | None = None


class DailyProductUpdate(DailyProductCreate):
    enabled: bool = True
    position: int = Field(ge=1, le=10000)


class DailyRunInProgress(RuntimeError):
    pass


@contextmanager
def daily_run_lock(data_dir: Path) -> Iterator[None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "daily-run.lock"
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise DailyRunInProgress("자동 생성 작업이 이미 실행 중입니다.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


class DailyRunItem(BaseModel):
    product_id: str
    product_name: str
    status: Literal["succeeded", "failed"]
    project_id: str | None = None
    draft_download_url: str | None = None
    script_fingerprint: str | None = None
    error_message: str | None = None


class DailyRun(BaseModel):
    id: str
    status: Literal["succeeded", "partial", "failed"]
    started_at: str
    finished_at: str
    success_count: int
    failure_count: int
    items: list[DailyRunItem]


BuildOne = Callable[[DailyProduct, int], Awaitable[dict]]
AutoBuilder = Callable[[object, str, AutoBuildOptions], Awaitable[dict]]


async def build_daily_item(
    services: object,
    product: DailyProduct,
    variation_index: int,
    auto_builder: AutoBuilder = run_auto_build,
) -> dict:
    tones = ("friend", "shopping", "calm_review")
    content_types = ("problem_solution", "comparison", "situational")
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H%M")
    project = services.projects.create(
        ProjectCreate(
            name=f"자동 {timestamp} {product.product_name}"[:80],
            affiliate_url=product.affiliate_url,
            product_name=product.product_name,
            category=product.category,
            features=product.features,
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )
    options = AutoBuildOptions(
        query=f"{product.product_name} {product.category}",
        media_type="video",
        asset_count=3,
        tone=tones[variation_index % len(tones)],
        content_type=content_types[
            (variation_index // len(tones)) % len(content_types)
        ],
        pexels_page=(variation_index % 80) + 1,
    )
    result = await auto_builder(services, project.id, options)
    script_path = (
        Path(services.settings.data_dir) / "projects" / project.id / "script.json"
    )
    if script_path.is_file() and not script_path.is_symlink():
        result["script_fingerprint"] = sha256(script_path.read_bytes()).hexdigest()
    result.setdefault("project_id", project.id)
    return result


class DailyQueueRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(self, payload: DailyProductCreate) -> DailyProduct:
        product_id = str(uuid4())
        now = utc_now()
        with self.database.connection() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM daily_products"
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO daily_products (
                    id, product_name, category, affiliate_url, features_json,
                    enabled, position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    product_id,
                    payload.product_name.strip(),
                    payload.category.strip(),
                    str(payload.affiliate_url),
                    json.dumps(payload.features, ensure_ascii=False),
                    position,
                    now,
                    now,
                ),
            )
        return self.get(product_id)

    def get(self, product_id: str) -> DailyProduct:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM daily_products WHERE id = ?", (product_id,)
            ).fetchone()
        if row is None:
            raise LookupError(product_id)
        return DailyProduct(
            id=row["id"],
            product_name=row["product_name"],
            category=row["category"],
            affiliate_url=row["affiliate_url"],
            features=json.loads(row["features_json"]),
            enabled=bool(row["enabled"]),
            position=row["position"],
            last_run_at=row["last_run_at"],
        )

    def list(self) -> list[DailyProduct]:
        with self.database.connection() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM daily_products ORDER BY position, created_at"
                )
            ]
        return [self.get(product_id) for product_id in ids]

    def delete(self, product_id: str) -> None:
        with self.database.connection() as conn:
            result = conn.execute(
                "DELETE FROM daily_products WHERE id = ?", (product_id,)
            )
            if result.rowcount == 0:
                raise LookupError(product_id)

    def update(self, product_id: str, payload: DailyProductUpdate) -> DailyProduct:
        now = utc_now()
        with self.database.connection() as conn:
            result = conn.execute(
                """UPDATE daily_products SET
                    product_name = ?, category = ?, affiliate_url = ?,
                    features_json = ?, enabled = ?, position = ?, updated_at = ?
                WHERE id = ?""",
                (
                    payload.product_name.strip(),
                    payload.category.strip(),
                    str(payload.affiliate_url),
                    json.dumps(payload.features, ensure_ascii=False),
                    int(payload.enabled),
                    payload.position,
                    now,
                    product_id,
                ),
            )
            if result.rowcount == 0:
                raise LookupError(product_id)
        return self.get(product_id)

    def next_enabled(self, limit: int) -> list[DailyProduct]:
        with self.database.connection() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    """SELECT id FROM daily_products WHERE enabled = 1
                    ORDER BY CASE WHEN last_run_at IS NULL THEN 0 ELSE 1 END,
                    last_run_at, position LIMIT ?""",
                    (limit,),
                )
            ]
        return [self.get(product_id) for product_id in ids]

    def mark_attempted(self, product_id: str) -> None:
        now = utc_now()
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE daily_products SET last_run_at = ?, updated_at = ? WHERE id = ?",
                (now, now, product_id),
            )

    def save_run(self, run: DailyRun) -> DailyRun:
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO daily_runs (
                    id, status, started_at, finished_at, success_count,
                    failure_count, items_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.id,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    run.success_count,
                    run.failure_count,
                    json.dumps(
                        [item.model_dump(mode="json") for item in run.items],
                        ensure_ascii=False,
                    ),
                ),
            )
        return run

    def list_runs(self, limit: int = 20) -> list[DailyRun]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            DailyRun(
                id=row["id"],
                status=row["status"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                success_count=row["success_count"],
                failure_count=row["failure_count"],
                items=json.loads(row["items_json"]),
            )
            for row in rows
        ]

    def attempt_count(self) -> int:
        with self.database.connection() as conn:
            return int(
                conn.execute(
                    "SELECT COALESCE(SUM(success_count + failure_count), 0) FROM daily_runs"
                ).fetchone()[0]
            )


class DailyRunner:
    def __init__(self, queue: DailyQueueRepository, build_one: BuildOne):
        self.queue = queue
        self.build_one = build_one

    async def run(self, limit: int = 2) -> DailyRun:
        started_at = utc_now()
        items: list[DailyRunItem] = []
        products = self.queue.next_enabled(limit)
        prior_count = self.queue.attempt_count()
        for index, product in enumerate(products):
            try:
                result = await self.build_one(product, prior_count + index)
                items.append(
                    DailyRunItem(
                        product_id=product.id,
                        product_name=product.product_name,
                        status="succeeded",
                        project_id=result.get("project_id"),
                        draft_download_url=result.get("draft_download_url"),
                        script_fingerprint=result.get("script_fingerprint"),
                    )
                )
            except Exception as exc:
                items.append(
                    DailyRunItem(
                        product_id=product.id,
                        product_name=product.product_name,
                        status="failed",
                        error_message=str(exc)[:500],
                    )
                )
            finally:
                self.queue.mark_attempted(product.id)
        success_count = sum(item.status == "succeeded" for item in items)
        failure_count = sum(item.status == "failed" for item in items)
        status = (
            "succeeded" if items and failure_count == 0
            else "failed" if success_count == 0
            else "partial"
        )
        run = DailyRun(
            id=str(uuid4()),
            status=status,
            started_at=started_at,
            finished_at=utc_now(),
            success_count=success_count,
            failure_count=failure_count,
            items=items,
        )
        return self.queue.save_run(run)


def main() -> int:
    from .app import Services
    from .config import Settings
    from .reel_automation import ReelDailyRunner, build_reel_item

    base_dir = Path(__file__).resolve().parents[2]
    settings = Settings.load(base_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        with daily_run_lock(settings.data_dir):
            services = Services(settings)
            runner = ReelDailyRunner(
                services.reel_automation,
                services.reel_planner,
                lambda record, plan: build_reel_item(services, record, plan),
            )
            run = asyncio.run(runner.run(datetime.now().astimezone().date(), count=2))
            print(run.model_dump_json(indent=2))
            return 0 if run.success_count > 0 else 1
    except DailyRunInProgress:
        print("이미 자동 생성 작업이 실행 중입니다.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
