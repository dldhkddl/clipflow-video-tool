import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import create_app
from clipflow.config import Settings


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (640, 640), "purple").save(buffer, format="PNG")
    return buffer.getvalue()


def test_page_loads_and_offline_project_flow_reaches_export(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "ClipFlow AI" in page.text

        response = client.post(
            "/api/projects",
            json={
                "name": "API 흐름",
                "affiliate_url": "https://link.coupang.com/a/example",
                "product_name": "미니 블렌더",
                "category": "생활가전",
                "features": ["420ml", "무선", "분리 세척"],
                "target_seconds": 30,
                "platforms": ["youtube", "instagram"],
            },
        )
        assert response.status_code == 201
        project = response.json()

        response = client.post(
            f"/api/projects/{project['id']}/assets",
            files={"file": ("sample.png", png_bytes(), "image/png")},
        )
        assert response.status_code == 201
        asset = response.json()
        assert client.put(
            f"/api/projects/{project['id']}/assets/{asset['id']}/rights",
            json={"approved": True},
        ).status_code == 200

        analysis = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={"asset_ids": [asset["id"]]},
        ).json()
        analysis["confirmed"] = True
        assert client.put(
            f"/api/projects/{project['id']}/analysis",
            json=analysis,
        ).status_code == 200

        assert client.post(
            f"/api/projects/{project['id']}/script",
            json={"content_type": "problem_solution", "tone": "friend"},
        ).status_code == 201
        assert client.post(f"/api/projects/{project['id']}/speech").status_code == 201
        assert client.post(
            f"/api/projects/{project['id']}/render",
            json={"profile": "preview"},
        ).status_code == 201

        attribution_path = (
            settings.data_dir / "projects" / project["id"] / "pexels-attributions.json"
        )
        attribution_path.write_text(
            json.dumps(
                [
                    {
                        "asset_id": asset["id"],
                        "creator": "Kim",
                        "creator_url": "https://www.pexels.com/@kim",
                        "media_url": "https://www.pexels.com/video/peach-101/",
                        "pexels_id": 101,
                        "type": "video",
                    }
                ]
            ),
            encoding="utf-8",
        )

        assert client.put(
            f"/api/projects/{project['id']}/review",
            json={
                "product_facts": True,
                "asset_rights": True,
                "honest_experience": True,
                "affiliate_disclosure": True,
            },
        ).status_code == 200
        response = client.post(f"/api/projects/{project['id']}/export")
        assert response.status_code == 200
        download = client.get(response.json()["download_url"])
        assert download.status_code == 200
        assert download.content.startswith(b"PK")
        with ZipFile(BytesIO(download.content)) as archive:
            credits = archive.read("pexels-credits.txt").decode("utf-8")
        assert "Kim" in credits
        assert "https://www.pexels.com/video/peach-101/" in credits


def test_review_incomplete_returns_recoverable_error(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = client.post(
            "/api/projects",
            json={
                "name": "검수 오류",
                "affiliate_url": "https://link.coupang.com/a/example",
                "product_name": "상품",
                "category": "생활",
                "features": ["특징"],
                "target_seconds": 30,
                "platforms": ["youtube"],
            },
        ).json()
        response = client.post(f"/api/projects/{project['id']}/export")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVIEW_INCOMPLETE"


def test_page_exposes_direct_higgsfield_to_instagram_workflow(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")
        script = client.get("/static/app.js?v=20260828-direct-studio")

    assert page.status_code == 200
    for required in (
        'id="sourceModeHiggsfield"',
        'id="sourceModeUpload"',
        'id="ownVideoFile"',
        'id="uploadOwnVideoButton"',
        'id="videoProfileReferenceFile"',
        'id="aiPrompt"',
        'id="videoDuration"',
        'id="videoGenerateAudio"',
        'id="costButton"',
        'id="generateVideoButton"',
        'id="latestVideo"',
        'id="generateSocialCopyButton"',
        'id="copyAnalysisStatus"',
        'id="socialDescription"',
        'id="socialHashtags"',
        'id="saveSocialCopyButton"',
        'id="instagramPublishButton"',
    ):
        assert required in page.text
    for removed in (
        "상품 분석",
        "AI 대본 생성",
        "대본 다시 수정",
        "AI 음성 생성",
        "자막",
        "최종 ZIP 저장",
        'data-step-target=',
        'id="scriptEditor"',
        'id="renderButton"',
    ):
        assert removed not in page.text
    assert "/social-copy/generate" in script.text
    assert "body?.error?.message" in script.text
    assert "/videos/upload" in script.text
    assert "/instagram/prepare" in script.text
    assert "/instagram/publish" in script.text


def test_simplified_page_does_not_initialize_removed_automation(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        script = client.get("/static/app.js?v=20260810")

    assert script.status_code == 200
    for removed_reference in (
        '$("#pexelsPanel")',
        '$("#pexelsSearchButton")',
        '$("#youtubePanel")',
        '$("#addReferenceButton")',
        '$("#autoBuildButton")',
        '$("#dailyProductForm")',
        '$("#dailyRunNowButton")',
        'api("/api/pexels/status")',
        'api("/api/daily/products")',
        'api("/api/daily/runs")',
    ):
        assert removed_reference not in script.text


def test_page_uses_dark_creator_studio_layout(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")
        styles = client.get("/static/app.css?v=20260828-direct-studio")
        script = client.get("/static/app.js?v=20260828-direct-studio")

    assert 'class="creator-theme"' in page.text
    assert 'class="direct-studio"' in page.text
    assert 'class="creation-panel panel-card"' in page.text
    assert 'class="result-panel panel-card"' in page.text
    assert 'class="editorial-theme"' not in page.text
    assert "/static/app.css?v=20260901-video-upload" in page.text
    assert "--creator-bg:#0b0b10" in styles.text
    assert ".direct-studio" in styles.text
    assert "@media(max-width:900px)" in styles.text
    assert 'class="creator-appbar"' in page.text
    assert 'id="higgsfieldCredit"' in page.text
    assert 'id="higgsfieldCreditValue"' in page.text
    assert 'id="metaConnection"' in page.text
    assert 'id="metaConnectionValue"' in page.text
    assert 'id="instagramPublishButton"' in page.text
    assert 'id="projectSelect"' in page.text
    assert 'renderHiggsfieldStatus(status)' in script.text
    assert 'renderMetaStatus(status)' in script.text
    assert 'api("/api/meta/status")' in script.text
    assert '/instagram/prepare' in script.text
    assert '/instagram/status' in script.text
    assert '/instagram/publish' in script.text
    assert 'publishToInstagram()' in script.text
    assert 'status.credits.toLocaleString("ko-KR")' in script.text


def test_project_controls_remain_available_in_direct_studio(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/")

    assert 'id="projectSelect"' in page.text
    assert 'id="newProjectButton"' in page.text
    assert 'id="deleteProjectButton"' in page.text
