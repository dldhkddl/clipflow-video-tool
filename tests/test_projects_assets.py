from io import BytesIO

import pytest
from PIL import Image

from clipflow.assets import AssetService, RightsNotApproved
from clipflow.db import Database
from clipflow.models import ProjectCreate
from clipflow.projects import ProjectRepository


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    return database


@pytest.fixture
def project(db):
    return ProjectRepository(db).create(
        ProjectCreate(
            name="미니 블렌더",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="무선 미니 블렌더",
            category="생활가전",
            features=["420ml", "무선"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "purple").save(buffer, format="PNG")
    return buffer.getvalue()


def test_create_project_normalizes_affiliate_link(db):
    project = ProjectRepository(db).create(
        ProjectCreate(
            name="미니 블렌더",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="무선 미니 블렌더",
            category="생활가전",
            features=["420ml", "무선"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )
    assert str(project.affiliate_url).startswith("https://link.coupang.com/")


def test_asset_cannot_be_used_before_rights_approval(db, project, tmp_path):
    service = AssetService(db, tmp_path / "projects")
    asset = service.save(project.id, "sample.png", "image/png", png_bytes())
    assert asset.rights_approved is False
    with pytest.raises(RightsNotApproved):
        service.require_renderable([asset.id])
    service.approve_rights(asset.id, True)
    assert service.require_renderable([asset.id])[0].rights_approved is True


def test_rejects_path_traversal_filename(db, project, tmp_path):
    service = AssetService(db, tmp_path / "projects")
    with pytest.raises(ValueError, match="파일명"):
        service.save(project.id, "../sample.png", "image/png", png_bytes())


def test_rejects_mime_mismatch(db, project, tmp_path):
    service = AssetService(db, tmp_path / "projects")
    with pytest.raises(ValueError, match="형식"):
        service.save(project.id, "sample.jpg", "image/jpeg", png_bytes())
