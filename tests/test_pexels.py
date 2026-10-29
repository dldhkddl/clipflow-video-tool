import json

import pytest
from fastapi.testclient import TestClient

from clipflow.app import create_app
from clipflow.assets import AssetService
from clipflow.config import Settings
from clipflow.db import Database
from clipflow.models import ProjectCreate
from clipflow.pexels import PexelsNotConfigured, PexelsService
from clipflow.projects import ProjectRepository


def make_project(tmp_path):
    db = Database(tmp_path / "clipflow.sqlite3")
    db.initialize()
    project = ProjectRepository(db).create(
        ProjectCreate(
            name="Pexels 테스트",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="복숭아",
            category="농수산물",
            features=["과즙"],
            target_seconds=30,
            platforms={"youtube"},
        )
    )
    return db, project


def test_search_requires_free_api_key(tmp_path):
    db, _project = make_project(tmp_path)
    service = PexelsService(None, AssetService(db, tmp_path / "projects"), tmp_path / "projects")

    with pytest.raises(PexelsNotConfigured):
        service.search("복숭아", "video")


def test_video_search_returns_portrait_preview_and_credit(tmp_path):
    db, _project = make_project(tmp_path)
    calls = []

    def json_get(path, params):
        calls.append((path, params))
        return {
            "videos": [
                {
                    "id": 101,
                    "width": 720,
                    "height": 1280,
                    "duration": 8,
                    "url": "https://www.pexels.com/video/peach-101/",
                    "image": "https://images.pexels.com/videos/101/preview.jpg",
                    "user": {"name": "Kim", "url": "https://www.pexels.com/@kim"},
                    "video_files": [
                        {
                            "file_type": "video/mp4",
                            "width": 720,
                            "height": 1280,
                            "quality": "hd",
                            "link": "https://videos.pexels.com/video-files/101/101-hd.mp4",
                        }
                    ],
                }
            ]
        }

    service = PexelsService(
        "free-key",
        AssetService(db, tmp_path / "projects"),
        tmp_path / "projects",
        json_get=json_get,
    )

    result = service.search("복숭아", "video")

    assert calls == [
        ("/v1/videos/search", {"query": "복숭아", "orientation": "portrait", "locale": "ko-KR", "per_page": 8, "page": 1})
    ]
    assert result[0].id == 101
    assert str(result[0].preview_url).endswith("preview.jpg")
    assert str(result[0].media_url).endswith("101-hd.mp4")
    assert result[0].creator == "Kim"


def test_imported_video_is_approved_and_attribution_is_saved(tmp_path):
    db, project = make_project(tmp_path)
    detail = {
        "id": 101,
        "width": 720,
        "height": 1280,
        "duration": 8,
        "image": "https://images.pexels.com/videos/101/preview.jpg",
        "url": "https://www.pexels.com/video/peach-101/",
        "user": {"name": "Kim", "url": "https://www.pexels.com/@kim"},
        "video_files": [
            {
                "file_type": "video/mp4",
                "width": 720,
                "height": 1280,
                "quality": "hd",
                "link": "https://videos.pexels.com/video-files/101/101-hd.mp4",
            }
        ],
    }
    service = PexelsService(
        "free-key",
        AssetService(db, tmp_path / "projects"),
        tmp_path / "projects",
        json_get=lambda _path, _params: detail,
        bytes_get=lambda _url, _limit: (b"fake-mp4", "video/mp4"),
    )

    asset = service.import_item(project.id, "video", 101)

    assert asset.filename == "pexels-video-101.mp4"
    assert asset.rights_approved is True
    credits = json.loads(
        (tmp_path / "projects" / project.id / "pexels-attributions.json").read_text(encoding="utf-8")
    )
    assert credits == [
        {
            "asset_id": asset.id,
            "creator": "Kim",
            "creator_url": "https://www.pexels.com/@kim",
            "media_url": "https://www.pexels.com/video/peach-101/",
            "pexels_id": 101,
            "type": "video",
        }
    ]


def test_api_reports_missing_key_with_setup_guidance(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        status = client.get("/api/pexels/status")
        project = client.post(
            "/api/projects",
            json={
                "name": "Pexels API",
                "affiliate_url": "https://link.coupang.com/a/example",
                "product_name": "복숭아",
                "category": "농수산물",
                "features": ["과즙"],
                "target_seconds": 30,
                "platforms": ["youtube"],
            },
        ).json()
        search = client.get(
            f"/api/projects/{project['id']}/pexels/search",
            params={"query": "복숭아", "media_type": "video"},
        )

    assert status.json()["configured"] is False
    assert status.json()["setup_url"] == "https://www.pexels.com/api/"
    assert search.status_code == 409
    assert search.json()["error"]["code"] == "PEXELS_NOT_CONFIGURED"
