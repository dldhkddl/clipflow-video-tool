import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import create_app
from clipflow.config import Settings
from clipflow.pexels import PexelsService


def jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (720, 1280), "peachpuff").save(buffer, format="JPEG")
    return buffer.getvalue()


def pexels_photo(photo_id: int) -> dict:
    return {
        "id": photo_id,
        "width": 720,
        "height": 1280,
        "url": f"https://www.pexels.com/photo/{photo_id}/",
        "photographer": f"Creator {photo_id}",
        "photographer_url": f"https://www.pexels.com/@creator-{photo_id}/",
        "src": {
            "medium": f"https://images.pexels.com/photos/{photo_id}/preview.jpeg",
            "portrait": f"https://images.pexels.com/photos/{photo_id}/portrait.jpeg",
        },
    }


def create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={
            "name": "복숭아 자동 광고",
            "affiliate_url": "https://link.coupang.com/a/example",
            "product_name": "복숭아",
            "category": "농수산물",
            "features": ["과즙이 풍부함", "향이 진함"],
            "target_seconds": 30,
            "platforms": ["youtube", "instagram"],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_auto_build_imports_pexels_and_saves_review_draft_zip(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True, pexels_api_key="test-key")
    photos = {photo_id: pexels_photo(photo_id) for photo_id in (11, 12, 13)}

    def json_get(path: str, _params: dict[str, object]) -> dict:
        if path == "/v1/search":
            return {"photos": list(photos.values())}
        return photos[int(path.rsplit("/", 1)[-1])]

    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        services.pexels = PexelsService(
            "test-key",
            services.assets,
            settings.data_dir / "projects",
            json_get=json_get,
            bytes_get=lambda _url, _limit: (jpeg_bytes(), "image/jpeg"),
        )
        previous_project = settings.data_dir / "projects" / "previous-project"
        previous_project.mkdir(parents=True)
        (previous_project / "pexels-attributions.json").write_text(
            json.dumps([{"pexels_id": 11}], ensure_ascii=False), encoding="utf-8"
        )
        project = create_project(client)
        assert client.put(
            f"/api/projects/{project['id']}/review",
            json={
                "product_facts": True,
                "asset_rights": True,
                "honest_experience": True,
                "affiliate_disclosure": True,
            },
        ).status_code == 200

        response = client.post(
            f"/api/projects/{project['id']}/auto-build",
            json={
                "query": "복숭아 농수산물",
                "media_type": "image",
                "asset_count": 2,
                "tone": "friend",
            },
        )

        assert response.status_code == 201
        result = response.json()
        assert result["status"] == "completed"
        assert result["asset_count"] == 2
        assert result["review_required"] is True
        assert result["completed_steps"] == [
            "assets",
            "analysis",
            "script",
            "speech",
            "video",
            "draft_zip",
        ]
        assert result["draft_download_url"].endswith(f"/{project['id']}-draft.zip")

        workspace = client.get(f"/api/projects/{project['id']}/workspace").json()
        assert workspace["max_step"] == 5
        assert len(workspace["assets"]) == 2
        assert all(asset["rights_approved"] for asset in workspace["assets"])
        assert [
            item["pexels_id"]
            for item in services.pexels.list_attributions(project["id"])
        ] == [12, 13]
        assert workspace["analysis"]["confirmed"] is True
        assert workspace["script"]["scenes"]
        assert workspace["review"]["reviewed_at"] is None
        assert not any(
            workspace["review"][key]
            for key in (
                "product_facts",
                "asset_rights",
                "honest_experience",
                "affiliate_disclosure",
            )
        )

        final_export = client.post(f"/api/projects/{project['id']}/export")
        assert final_export.status_code == 409
        assert final_export.json()["error"]["code"] == "REVIEW_INCOMPLETE"

        download = client.get(result["draft_download_url"])
        assert download.status_code == 200
        with ZipFile(BytesIO(download.content)) as archive:
            assert "video-vertical.mp4" in archive.namelist()
            summary = json.loads(archive.read("project-summary.json"))
        assert summary["review_status"] == "draft_needs_review"


def test_page_hides_legacy_one_click_auto_build_controls(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert 'id="autoBuildButton"' not in page.text
    assert 'id="autoBuildMediaType"' not in page.text
    assert 'id="autoBuildTone"' not in page.text
