import pytest

from clipflow.costs import CostGuard, MonthlyCostLimitExceeded
from clipflow.db import Database
from clipflow.jobs import JobRepository, normalized_hash
from clipflow.models import AnalysisItem, ProductAnalysis, ProjectCreate, ScriptRequest
from clipflow.projects import ProjectRepository
from clipflow.providers.fake import FakeScriptProvider


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    return db


@pytest.fixture
def project(database):
    return ProjectRepository(database).create(
        ProjectCreate(
            name="테스트",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="무선 미니 블렌더",
            category="생활가전",
            features=["420ml", "무선", "분리 세척"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )


@pytest.mark.asyncio
async def test_fake_script_has_required_structure(project):
    draft = await FakeScriptProvider().generate(
        ScriptRequest.from_project(project, content_type="problem_solution")
    )
    assert draft.hook
    assert draft.selection_criteria
    assert draft.pros
    assert draft.cons
    assert draft.cta
    assert 4 <= len(draft.scenes) <= 7
    assert sum(scene.duration_seconds for scene in draft.scenes) == 30


@pytest.mark.asyncio
async def test_friend_tone_uses_confirmed_analysis_and_avoids_generic_ai_copy(project):
    analysis = ProductAnalysis(
        product_name=project.product_name,
        category=project.category,
        visible_features=[AnalysisItem(text="한 손에 들어오는 크기", origin="visual")],
        package_text=[],
        taste_notes=[],
        use_cases=[AnalysisItem(text="바쁜 후 바로 갈아 마시기", origin="user")],
        promotion_points=[AnalysisItem(text="무선", origin="user")],
        visual_direction=[AnalysisItem(text="밝고 산뜻한 분위기", origin="ai_promotion")],
        source_asset_ids=["asset-1"],
        mode="vision",
        confirmed=True,
    )
    request = ScriptRequest.from_project(
        project,
        content_type="problem_solution",
        analysis=analysis,
        tone="friend",
    )

    draft = await FakeScriptProvider().generate(request)

    copy = " ".join(
        [draft.hook, *(scene.on_screen_text for scene in draft.scenes), *(scene.narration for scene in draft.scenes)]
    )
    assert "무선" in copy
    assert "바쁜 후 바로 갈아 마시기" in copy
    assert "선택 기준" not in copy
    assert "사용 환경의 한계" not in copy
    assert all(len(scene.on_screen_text) <= 22 for scene in draft.scenes)


@pytest.mark.asyncio
async def test_food_promotion_copy_uses_eating_language_without_repeated_feeling(project):
    food_project = project.model_copy(
        update={"product_name": "복숭아", "category": "농수산물", "features": ["과즙이 풍부함"]}
    )
    analysis = ProductAnalysis(
        product_name="복숭아",
        category="농수산물",
        visible_features=[],
        package_text=[],
        taste_notes=[AnalysisItem(text="달콤하고 과즙이 팡 터지는 느낌", origin="user")],
        use_cases=[AnalysisItem(text="집에서 시원하게 즐기기", origin="user")],
        promotion_points=[AnalysisItem(text="과즙이 풍부함", origin="user")],
        visual_direction=[],
        source_asset_ids=["asset-1"],
        mode="vision",
        confirmed=True,
    )

    draft = await FakeScriptProvider().generate(
        ScriptRequest.from_project(food_project, "problem_solution", analysis, "friend")
    )
    copy = " ".join(scene.narration for scene in draft.scenes)

    assert "맛있게 즐기기 좋은" in copy
    assert "자주 쓰기 좋은" not in copy
    assert "느낌라는 느낌" not in copy
    assert "풍부함이라는" not in copy
    assert ". 이런 " not in copy


def test_cost_guard_blocks_monthly_limit(database):
    guard = CostGuard(database, warning_krw=50_000, limit_krw=100_000)
    guard.record(99_500, kind="script", provider="test", model="fake")
    with pytest.raises(MonthlyCostLimitExceeded):
        guard.authorize(600)


def test_normalized_hash_ignores_key_order():
    assert normalized_hash({"b": 2, "a": 1}) == normalized_hash({"a": 1, "b": 2})


def test_successful_job_is_reused(database, project):
    jobs = JobRepository(database)
    job = jobs.create(project.id, "script", "same-input")
    jobs.mark_succeeded(job.id, "script.json")
    reused = jobs.get_succeeded(project.id, "script", "same-input")
    assert reused is not None
    assert reused.id == job.id
