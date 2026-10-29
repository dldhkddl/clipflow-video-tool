from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import create_app
from clipflow.config import Settings
from test_workspace_api import create_project


def reference_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (320, 480), "peachpuff").save(buffer, format="PNG")
    return buffer.getvalue()


def test_offline_ai_image_cost_and_generation_are_previewable(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)

        cost = client.post(
            f"/api/projects/{project['id']}/ai-assets/cost",
            json={"kind": "image", "prompt": "복숭아 스튜디오 제품 사진"},
        )
        assert cost.status_code == 200
        assert cost.json() == {
            "credits": 0,
            "offline_demo": True,
            "requires_confirmation": True,
        }

        blocked = client.post(
            f"/api/projects/{project['id']}/ai-assets",
            json={
                "kind": "image",
                "prompt": "복숭아 스튜디오 제품 사진",
                "confirmed_cost": False,
            },
        )
        assert blocked.status_code == 409

        response = client.post(
            f"/api/projects/{project['id']}/ai-assets",
            json={
                "kind": "image",
                "prompt": "복숭아 스튜디오 제품 사진",
                "confirmed_cost": True,
            },
        )
        assert response.status_code == 201
        asset = response.json()["assets"][-1]
        assert asset["media_type"] == "image"
        assert asset["rights_approved"] is True
        media = client.get(
            f"/api/projects/{project['id']}/assets/{asset['id']}/media"
        )
        assert media.status_code == 200
        assert media.content.startswith(b"\x89PNG")


def test_offline_ai_video_creates_start_image_when_missing(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)

        response = client.post(
            f"/api/projects/{project['id']}/ai-assets",
            json={
                "kind": "video",
                "prompt": "복숭아를 천천히 도는 카메라",
                "confirmed_cost": True,
            },
        )

        assert response.status_code == 201
        assets = response.json()["assets"]
        assert [asset["media_type"] for asset in assets] == ["image", "video"]
        assert all(asset["rights_approved"] for asset in assets)
        video = assets[-1]
        media = client.get(
            f"/api/projects/{project['id']}/assets/{video['id']}/media"
        )
        assert media.status_code == 200
        assert media.headers["content-type"] == "video/mp4"
        assert len(media.content) > 1_000

        final_video = client.get(f"/api/projects/{project['id']}/media/video")
        assert final_video.status_code == 200
        assert final_video.content == media.content


def test_higgsfield_status_explains_offline_demo(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        response = client.get("/api/higgsfield/status")

        assert response.status_code == 200
        assert response.json()["offline_demo"] is True
        assert response.json()["authenticated"] is False


def test_video_profile_is_saved_and_reused_after_restart(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    payload = {
        "enabled": True,
        "base_prompt": "실제 강아지와 과일 상자가 함께 있는 자연스러운 주방 광고",
        "negative_prompt": "흐린 배경, 보케, 달라진 털무늬",
        "lens_mm": 35,
        "aperture": "f/8",
        "camera_motion": "고정 또는 매우 느린 이동",
    }

    with TestClient(create_app(settings)) as client:
        saved = client.put("/api/video-profile", json=payload)
        assert saved.status_code == 200
        assert saved.json() == payload

    with TestClient(create_app(settings)) as client:
        loaded = client.get("/api/video-profile")
        assert loaded.status_code == 200
        assert loaded.json() == payload


def test_video_cost_automatically_combines_saved_profile_with_daily_scene(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        client.put(
            "/api/video-profile",
            json={
                "enabled": True,
                "base_prompt": "강아지와 주방 배경 전체가 선명함",
                "negative_prompt": "보케, 흐린 배경",
                "lens_mm": 35,
                "aperture": "f/8",
                "camera_motion": "고정 촬영",
            },
        )

        response = client.post(
            f"/api/projects/{project['id']}/ai-assets/cost",
            json={"kind": "video", "prompt": "복숭아 상자를 바라보는 강아지"},
        )

        assert response.status_code == 200
        effective_prompt = response.json()["effective_prompt"]
        assert "강아지와 주방 배경 전체가 선명함" in effective_prompt
        assert "35mm" in effective_prompt
        assert "f/8" in effective_prompt
        assert "피해야 할 표현: 보케, 흐린 배경" in effective_prompt
        assert effective_prompt.endswith("오늘의 장면: 복숭아 상자를 바라보는 강아지")


def test_video_profile_can_be_skipped_for_one_generation(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/ai-assets/cost",
            json={
                "kind": "video",
                "prompt": "이번 영상만 다른 연출",
                "use_video_profile": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["effective_prompt"] == "이번 영상만 다른 연출"


def test_direct_higgsfield_video_keeps_prompt_and_returns_selected_settings(tmp_path):
    prompt = "줄리아가 책상 아래로 미끄러지며 한국어로 '어버버!'라고 말한다."
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/ai-assets/cost",
            json={
                "kind": "video",
                "prompt": prompt,
                "use_video_profile": False,
                "video_duration": 8,
                "video_aspect_ratio": "auto",
                "video_resolution": "720p",
                "video_bitrate_mode": "high",
                "video_generate_audio": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["effective_prompt"] == prompt
    assert response.json()["video_settings"] == {
        "model": "seedance_2_0",
        "duration": 8,
        "aspect_ratio": "auto",
        "resolution": "720p",
        "bitrate_mode": "high",
        "generate_audio": True,
    }


def test_reference_image_can_be_used_without_static_video_profile(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        uploaded = client.post(
            "/api/video-profile/reference-image",
            files={"file": ("dog.png", reference_png(), "image/png")},
        )
        assert uploaded.status_code == 201
        project = create_project(client)

        response = client.post(
            f"/api/projects/{project['id']}/ai-assets/cost",
            json={
                "kind": "video",
                "prompt": "서류가 흩날리자 강아지가 좌우로 빠르게 뛰어다닌다",
                "use_video_profile": False,
                "use_profile_reference": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["effective_prompt"] == (
            "서류가 흩날리자 강아지가 좌우로 빠르게 뛰어다닌다"
        )
        assert response.json()["profile_reference_applied"] is True


def test_reference_image_controls_are_available_without_legacy_profile_editor(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        html = client.get("/").text

    assert 'id="useProfileReference"' in html
    assert '<input id="useProfileReference" type="checkbox" checked>' in html
    assert 'id="videoProfileReferenceFile"' in html
    assert 'id="uploadVideoProfileReferenceButton"' in html
    assert 'id="videoProfileForm"' not in html
    assert 'id="videoProfileBasePrompt"' not in html


def test_higgsfield_direct_controls_match_web_defaults(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        html = client.get("/").text

    assert 'id="videoModel"' in html
    assert '<option value="seedance_2_0" selected>Seedance 2.0</option>' in html
    assert 'id="videoDuration"' in html
    assert '<option value="8" selected>8초</option>' in html
    assert 'id="videoAspectRatio"' in html
    assert '<option value="auto" selected>자동</option>' in html
    assert 'id="videoResolution"' in html
    assert '<option value="720p" selected>720p</option>' in html
    assert 'id="videoBitrateMode"' in html
    assert '<option value="high" selected>높음</option>' in html
    assert 'id="videoGenerateAudio" type="checkbox" checked' in html
    assert 'maxlength="5000"' in html
    assert "설정에 따라 5초 또는 8초" in html


def test_brand_reference_image_is_uploaded_and_reused_as_video_start(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        empty = client.get("/api/video-profile/reference-image/status")
        assert empty.status_code == 200
        assert empty.json() == {"exists": False, "media_url": None}

        uploaded = client.post(
            "/api/video-profile/reference-image",
            files={"file": ("dog-and-kitchen.png", reference_png(), "image/png")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json() == {
            "exists": True,
            "media_url": "/api/video-profile/reference-image/media",
        }
        media = client.get(uploaded.json()["media_url"])
        assert media.status_code == 200
        assert media.headers["content-type"] == "image/png"

        project = create_project(client)
        generated = client.post(
            f"/api/projects/{project['id']}/ai-assets",
            json={
                "kind": "video",
                "prompt": "복숭아 상자를 바라보는 강아지",
                "confirmed_cost": True,
            },
        )

        assert generated.status_code == 201
        assert [item["media_type"] for item in generated.json()["assets"]] == ["video"]
        assert generated.json()["profile_reference_applied"] is True
