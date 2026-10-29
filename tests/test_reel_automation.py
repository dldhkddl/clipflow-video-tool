import asyncio
from datetime import date

from clipflow.db import Database
from clipflow.app import create_app
from clipflow.config import Settings
from clipflow.models import ProjectCreate
from clipflow.projects import ProjectRepository
from fastapi.testclient import TestClient
from clipflow.reel_automation import (
    ReelAutomationRepository,
    ReelDailyRunner,
    ReelKind,
    ReelPlanner,
    build_original_bgm,
)


def test_planner_rotates_fun_info_and_promo_without_copyrighted_song_requests():
    planner = ReelPlanner(site_name="샘플업체", dog_name="줄라이")

    plans = [planner.plan(index) for index in range(6)]

    assert [plan.kind for plan in plans] == [
        ReelKind.FUN,
        ReelKind.INFO,
        ReelKind.PROMO,
        ReelKind.FUN,
        ReelKind.INFO,
        ReelKind.PROMO,
    ]
    assert all(plan.duration_seconds == 15 for plan in plans)
    assert all(len(plan.scenes) == 3 for plan in plans)
    combined = " ".join(
        scene.prompt for plan in plans for scene in plan.scenes
    ).lower()
    for forbidden in ("에이티즈", "bad", "배드", "원곡", "챌린지 안무"):
        assert forbidden not in combined
    assert plans[0].captions_enabled is False
    assert plans[1].captions_enabled is True
    assert plans[2].scenes[-1].caption


def test_planner_creates_multiline_description_and_korean_hashtags():
    planner = ReelPlanner(site_name="샘플업체", dog_name="줄라이")

    for plan in (planner.plan(0), planner.plan(1), planner.plan(2)):
        assert len([line for line in plan.description.splitlines() if line.strip()]) >= 3
        assert 8 <= len(plan.hashtags) <= 12
        assert len(plan.hashtags) == len(set(plan.hashtags))
        assert all(tag.startswith("#") for tag in plan.hashtags)
        assert all(tag[1:].isalpha() for tag in plan.hashtags)
        assert "#샘플업체" in plan.hashtags
        assert "#줄라이" in plan.hashtags
        assert plan.instagram_text().endswith(" ".join(plan.hashtags))


def test_repository_reserves_exactly_two_unique_slots_per_day(tmp_path):
    database = Database(tmp_path / "clipflow.sqlite3")
    database.initialize()
    repository = ReelAutomationRepository(database)

    first = repository.reserve_day(date(2026, 8, 28), count=2)
    second = repository.reserve_day(date(2026, 8, 28), count=2)

    assert [item.slot for item in first] == [1, 2]
    assert [item.id for item in second] == [item.id for item in first]
    assert all(item.status == "queued" for item in first)


def test_original_bgm_is_repeatable_and_creates_a_fifteen_second_wav(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"

    build_original_bgm(first, seed="2026-08-28-1")
    build_original_bgm(second, seed="2026-08-28-1")

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[:4] == b"RIFF"
    assert first.stat().st_size > 100_000


def test_windows_schedule_is_set_to_eight_am():
    script = (
        __import__("pathlib").Path(__file__).parents[1] / "install_daily_task.ps1"
    ).read_text(encoding="utf-8")
    assert '/ST "08:00"' in script


def test_daily_runner_creates_two_review_items_and_retries_once(tmp_path):
    database = Database(tmp_path / "clipflow.sqlite3")
    database.initialize()
    repository = ReelAutomationRepository(database)
    attempts: dict[int, int] = {}

    async def build(record, plan):
        attempts[record.slot] = attempts.get(record.slot, 0) + 1
        if record.slot == 2 and attempts[record.slot] == 1:
            raise RuntimeError("일시적인 생성 오류")
        return {"project_id": f"project-{record.slot}", "title": plan.title}

    runner = ReelDailyRunner(repository, ReelPlanner("샘플업체", "줄라이"), build)
    result = asyncio.run(runner.run(date(2026, 8, 28), count=2))

    assert result.success_count == 2
    assert attempts == {1: 1, 2: 2}
    saved = repository.list_day("2026-08-28")
    assert [item.status for item in saved] == ["review", "review"]
    assert [item.project_id for item in saved] == ["project-1", "project-2"]


def test_project_repository_accepts_fifteen_second_reel(tmp_path):
    database = Database(tmp_path / "clipflow.sqlite3")
    database.initialize()
    project = ProjectRepository(database).create(
        ProjectCreate(
            name="15초 자동 릴스",
            affiliate_url="https://example.com/fruit",
            product_name="샘플업체 과일",
            category="과일 생활",
            features=["줄라이와 보는 제철 과일"],
            target_seconds=15,
            platforms={"instagram"},
        )
    )
    assert project.target_seconds == 15


def test_offline_reel_build_produces_reviewable_fifteen_second_video(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        record = services.reel_automation.reserve_day(date(2026, 8, 28), count=1)[0]
        runner = ReelDailyRunner(
            services.reel_automation,
            services.reel_planner,
            lambda item, plan: services.reel_builder(services, item, plan),
        )
        result = asyncio.run(runner.run(date(2026, 8, 28), count=1))

        assert result.success_count == 1
        project_id = result.items[0].project_id
        workspace = client.get(f"/api/projects/{project_id}/workspace").json()
        assert workspace["project"]["target_seconds"] == 15
        assert workspace["max_step"] == 5
        video = settings.data_dir / "projects" / project_id / "video-vertical.mp4"
        assert video.is_file() and video.stat().st_size > 100_000


def test_reel_api_includes_saved_instagram_text(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        record = services.reel_automation.reserve_day(date(2026, 8, 28), count=1)[0]
        services.reel_automation.update(
            record.id,
            status="review",
            project_id="project-caption",
            title="줄라이의 사과 정보",
        )
        project_dir = settings.data_dir / "projects" / "project-caption"
        project_dir.mkdir(parents=True)
        saved = "오늘의 사과 정보입니다.\n\n#샘플업체 #줄라이 #사과"
        (project_dir / "인스타그램-문구.txt").write_text(saved, encoding="utf-8")

        items = client.get("/api/reels").json()

        assert items[0]["instagram_text"] == saved
