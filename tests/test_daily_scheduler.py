import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from clipflow.app import create_app
from clipflow.config import Settings
from clipflow.daily import (
    DailyProductCreate,
    DailyProductUpdate,
    DailyQueueRepository,
    DailyRunner,
    build_daily_item,
    daily_run_lock,
)
from clipflow.db import Database


def product(name: str) -> DailyProductCreate:
    return DailyProductCreate(
        product_name=name,
        category="생활용품",
        affiliate_url=f"https://link.coupang.com/a/{name}",
        features=["사용이 간편함", "보관이 편함"],
    )


def test_daily_queue_rotates_two_products_and_continues_after_failure(tmp_path):
    database = Database(tmp_path / "clipflow.sqlite3")
    database.initialize()
    queue = DailyQueueRepository(database)
    first = queue.add(product("상품A"))
    second = queue.add(product("상품B"))
    third = queue.add(product("상품C"))
    attempted: list[str] = []

    async def build_one(item, variation_index):
        attempted.append(item.product_name)
        if item.product_name == "상품B":
            raise RuntimeError("외부 서비스 실패")
        return {
            "project_id": f"project-{item.product_name}",
            "draft_download_url": f"/exports/{item.product_name}.zip",
            "script_fingerprint": f"fingerprint-{variation_index}",
        }

    runner = DailyRunner(queue, build_one)
    first_run = asyncio.run(runner.run(limit=2))
    second_run = asyncio.run(runner.run(limit=2))

    assert attempted == [first.product_name, second.product_name, third.product_name, first.product_name]
    assert first_run.success_count == 1
    assert first_run.failure_count == 1
    assert first_run.items[1].status == "failed"
    assert first_run.items[1].error_message == "외부 서비스 실패"
    assert second_run.success_count == 2
    assert [item.product_id for item in second_run.items] == [third.id, first.id]


def test_daily_product_api_adds_lists_and_deletes_products(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/daily/products",
            json=product("무선 청소기").model_dump(mode="json"),
        )
        assert created.status_code == 201
        product_id = created.json()["id"]

        listing = client.get("/api/daily/products")
        assert listing.status_code == 200
        assert [item["product_name"] for item in listing.json()] == ["무선 청소기"]
        assert listing.json()[0]["enabled"] is True

        updated = client.put(
            f"/api/daily/products/{product_id}",
            json={
                **product("수정한 청소기").model_dump(mode="json"),
                "enabled": False,
                "position": 7,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["product_name"] == "수정한 청소기"
        assert updated.json()["enabled"] is False
        assert updated.json()["position"] == 7

        deleted = client.delete(f"/api/daily/products/{product_id}")
        assert deleted.status_code == 204
        assert client.get("/api/daily/products").json() == []


def test_page_hides_daily_product_queue_and_run_history(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert 'id="dailyProductForm"' not in page.text
    assert 'id="dailyProductList"' not in page.text
    assert 'id="dailyRunHistory"' not in page.text
    assert 'id="dailyRunNowButton"' not in page.text


def test_page_script_does_not_initialize_removed_daily_form():
    script = (
        Path(__file__).parents[1] / "src" / "clipflow" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    assert "const dailyForm = event.currentTarget;" not in script
    assert "dailyForm.reset();" not in script
    assert "event.currentTarget.reset();" not in script


@pytest.mark.asyncio
async def test_build_daily_item_creates_project_and_rotates_copy_style(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        product_item = services.daily_queue.add(product("복숭아"))
        captured = []

        async def fake_auto_builder(service, project_id, options):
            captured.append((service.projects.get(project_id), options))
            return {
                "project_id": project_id,
                "draft_download_url": f"/api/exports/{project_id}-draft.zip",
            }

        first = await build_daily_item(services, product_item, 0, fake_auto_builder)
        second = await build_daily_item(services, product_item, 1, fake_auto_builder)
        fourth = await build_daily_item(services, product_item, 3, fake_auto_builder)

    assert first["project_id"] != second["project_id"]
    assert captured[0][0].product_name == "복숭아"
    assert captured[0][1].tone != captured[1][1].tone
    assert captured[0][1].content_type != captured[2][1].content_type
    assert captured[0][1].pexels_page == 1
    assert captured[1][1].pexels_page == 2
    assert captured[2][1].pexels_page == 4
    assert fourth["project_id"] not in {first["project_id"], second["project_id"]}


def test_run_now_api_builds_two_products_and_records_history(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        services.daily_queue.add(product("A"))
        services.daily_queue.add(product("B"))

        async def fake_builder(_services, product, variation_index):
            return {
                "project_id": f"project-{product.product_name}",
                "draft_download_url": f"/download/{variation_index}",
            }

        services.daily_builder = fake_builder
        response = client.post("/api/daily/run-now")

        assert response.status_code == 201
        assert response.json()["success_count"] == 2
        history = client.get("/api/daily/runs").json()
        assert len(history) == 1
        assert {item["product_name"] for item in history[0]["items"]} == {"A", "B"}


def test_run_now_returns_conflict_while_shared_lock_is_held(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "daily-run.lock").write_text("stale file is harmless", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        with daily_run_lock(settings.data_dir):
            response = client.post("/api/daily/run-now")
    assert response.status_code == 409


def test_variation_sequence_uses_all_historical_attempts(tmp_path):
    database = Database(tmp_path / "clipflow.sqlite3")
    database.initialize()
    queue = DailyQueueRepository(database)
    item = queue.add(product("장기 상품"))
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO daily_runs (
                id, status, started_at, finished_at, success_count,
                failure_count, items_json
            ) VALUES ('old', 'succeeded', '2020', '2020', 2500, 0, '[]')"""
        )
    indexes = []

    async def build_one(_item, variation_index):
        indexes.append(variation_index)
        return {"project_id": "new"}

    asyncio.run(DailyRunner(queue, build_one).run(limit=1))
    assert indexes == [2500]
