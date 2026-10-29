import json
from zipfile import ZipFile

import pytest

from clipflow.db import Database
from clipflow.export import ExportService, ReviewIncomplete
from clipflow.models import ProjectCreate, ReviewState
from clipflow.projects import ProjectRepository


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    projects = ProjectRepository(db)
    project = projects.create(
        ProjectCreate(
            name="내보내기 테스트",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="미니 블렌더",
            category="생활가전",
            features=["420ml", "무선"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )
    project_dir = tmp_path / "projects" / project.id
    project_dir.mkdir(parents=True)
    (project_dir / "video-vertical.mp4").write_bytes(b"fake-mp4")
    service = ExportService(db, projects, tmp_path / "projects", tmp_path / "exports")
    return db, projects, project, service


def test_export_is_blocked_until_all_reviews_are_true(setup):
    _, _, project, service = setup
    with pytest.raises(ReviewIncomplete):
        service.build(project.id)


def test_export_contains_platform_copy_and_no_secret(setup, monkeypatch):
    _, projects, project, service = setup
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    projects.update_review(
        project.id,
        ReviewState(
            product_facts=True,
            asset_rights=True,
            honest_experience=True,
            affiliate_disclosure=True,
        ),
    )
    package = service.build(project.id)
    with ZipFile(package.zip_path) as archive:
        names = set(archive.namelist())
        payload = b"".join(archive.read(name) for name in names)
        summary = json.loads(archive.read("project-summary.json"))
    assert {
        "video-vertical.mp4",
        "youtube-description.txt",
        "instagram-caption.txt",
        "disclosure.txt",
        "project-summary.json",
    } <= names
    assert b"must-not-leak" not in payload
    assert summary["product_name"] == "미니 블렌더"
