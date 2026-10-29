from io import BytesIO

from PIL import Image

from clipflow.assets import AssetService
from clipflow.db import Database
from clipflow.models import AnalysisItem, ProductAnalysis, ProjectCreate
from clipflow.product_analysis import ProductAnalysisService
from clipflow.projects import ProjectRepository


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "peachpuff").save(buffer, format="PNG")
    return buffer.getvalue()


def test_offline_analysis_uses_product_details_and_persists_user_edits(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    project = ProjectRepository(database).create(
        ProjectCreate(
            name="복숭아 홍보",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="복숭아",
            category="농수산물",
            features=["과즙이 풍부함", "향이 진함"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )
    assets = AssetService(database, tmp_path / "projects")
    asset = assets.save(project.id, "peach.png", "image/png", png_bytes())
    asset = assets.approve_rights(asset.id, True)
    service = ProductAnalysisService(database, offline=True)

    generated = service.generate(project, [asset])

    assert generated.mode == "demo"
    assert generated.confirmed is False
    assert generated.product_name == "복숭아"
    assert any(item.text == "과즙이 풍부함" for item in generated.promotion_points)
    assert any(item.origin == "ai_promotion" for item in generated.taste_notes)

    edited = generated.model_copy(
        update={
            "confirmed": True,
            "taste_notes": [AnalysisItem(text="상큼하고 달콤한 맛", origin="user")],
        }
    )
    service.save(project.id, edited)
    restored = service.latest(project.id)

    assert restored is not None
    assert restored.confirmed is True
    assert restored.taste_notes == [AnalysisItem(text="상큼하고 달콤한 맛", origin="user")]


def test_analysis_requires_at_least_one_rights_approved_visual(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    project = ProjectRepository(database).create(
        ProjectCreate(
            name="테스트",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="상품",
            category="생활",
            features=["특징"],
            target_seconds=30,
            platforms={"youtube"},
        )
    )
    assets = AssetService(database, tmp_path / "projects")
    asset = assets.save(project.id, "sample.png", "image/png", png_bytes())
    service = ProductAnalysisService(database, offline=True)

    try:
        service.generate(project, [asset])
    except ValueError as exc:
        assert "권리" in str(exc)
    else:
        raise AssertionError("사용 권리를 확인하지 않은 자료가 분석됨")
