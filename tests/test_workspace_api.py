from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import create_app
from clipflow.config import Settings


def image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (320, 240), "peachpuff").save(buffer, format="PNG")
    return buffer.getvalue()


def create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={
            "name": "복숭아 숏폼",
            "affiliate_url": "https://link.coupang.com/a/example",
            "product_name": "복숭아",
            "category": "농수산물",
            "features": ["당도", "과즙"],
            "target_seconds": 30,
            "platforms": ["youtube", "instagram"],
        },
    )
    assert response.status_code == 201
    return response.json()


def upload_approved_asset(client: TestClient, project_id: str) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("peach.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 201
    asset = response.json()
    response = client.put(
        f"/api/projects/{project_id}/assets/{asset['id']}/rights",
        json={"approved": True},
    )
    assert response.status_code == 200
    return response.json()


def confirm_analysis(client: TestClient, project_id: str, asset_id: str) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/analysis",
        json={"asset_ids": [asset_id]},
    )
    assert response.status_code == 201
    analysis = response.json()
    analysis["confirmed"] = True
    response = client.put(f"/api/projects/{project_id}/analysis", json=analysis)
    assert response.status_code == 200
    return response.json()


def test_workspace_reports_reachable_step_and_previewable_assets(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)

        workspace = client.get(f"/api/projects/{project['id']}/workspace")
        assert workspace.status_code == 200
        assert workspace.json()["max_step"] == 2
        assert workspace.json()["assets"] == []
        assert workspace.json()["script"] is None
        assert workspace.json()["video_url"] is None

        asset = upload_approved_asset(client, project["id"])
        workspace = client.get(f"/api/projects/{project['id']}/workspace")
        assert workspace.status_code == 200
        assert workspace.json()["max_step"] == 2
        assert workspace.json()["analysis"] is None
        assert workspace.json()["assets"][0]["filename"] == "peach.png"
        assert workspace.json()["assets"][0]["rights_approved"] is True
        assert workspace.json()["assets"][0]["media_url"].endswith(
            f"/assets/{asset['id']}/media"
        )

        media = client.get(workspace.json()["assets"][0]["media_url"])
        assert media.status_code == 200
        assert media.headers["content-type"] == "image/png"
        assert media.content.startswith(b"\x89PNG")

        confirm_analysis(client, project["id"], asset["id"])
        workspace = client.get(f"/api/projects/{project['id']}/workspace").json()
        assert workspace["max_step"] == 3
        assert workspace["analysis"]["confirmed"] is True


def test_workspace_exposes_rendered_video_for_browser_playback(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload_approved_asset(client, project["id"])
        confirm_analysis(client, project["id"], asset["id"])
        assert client.post(
            f"/api/projects/{project['id']}/script",
            json={"content_type": "problem_solution"},
        ).status_code == 201

        workspace = client.get(f"/api/projects/{project['id']}/workspace")
        assert workspace.json()["max_step"] == 4
        assert workspace.json()["script"]["scenes"]

        assert client.post(f"/api/projects/{project['id']}/speech").status_code == 201
        assert client.post(
            f"/api/projects/{project['id']}/render", json={"profile": "preview"}
        ).status_code == 201

        workspace = client.get(f"/api/projects/{project['id']}/workspace")
        assert workspace.json()["max_step"] == 5
        assert workspace.json()["video_url"].endswith("/media/video")

        video = client.get(workspace.json()["video_url"])
        assert video.status_code == 200
        assert video.headers["content-type"] == "video/mp4"
        assert len(video.content) > 1_000


def test_edited_script_is_saved_as_latest_version(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload_approved_asset(client, project["id"])
        confirm_analysis(client, project["id"], asset["id"])
        generated = client.post(
            f"/api/projects/{project['id']}/script",
            json={"content_type": "problem_solution"},
        ).json()["draft"]
        generated["scenes"][0]["narration"] = "복숭아를 고를 때 향부터 확인하세요."
        generated["scenes"][0]["on_screen_text"] = "향이 진한 복숭아"
        generated["scenes"][0]["duration_seconds"] = 7.5
        generated["scenes"][0]["asset_id"] = asset["id"]
        generated["scenes"] = list(reversed(generated["scenes"]))
        generated["scenes"][-2]["duration_seconds"] = 4.5
        for index, scene in enumerate(generated["scenes"], start=1):
            scene["order"] = index

        response = client.put(
            f"/api/projects/{project['id']}/script", json=generated
        )

        assert response.status_code == 200
        assert response.json()["draft"]["scenes"][-1]["narration"] == (
            "복숭아를 고를 때 향부터 확인하세요."
        )
        workspace = client.get(f"/api/projects/{project['id']}/workspace").json()
        saved_scene = workspace["script"]["scenes"][-1]
        assert saved_scene["on_screen_text"] == "향이 진한 복숭아"
        assert saved_scene["duration_seconds"] == 7.5
        assert saved_scene["asset_id"] == asset["id"]
        assert [scene["order"] for scene in workspace["script"]["scenes"]] == [1, 2, 3, 4, 5]
        assert client.post(f"/api/projects/{project['id']}/speech").status_code == 201


def test_script_save_rejects_scene_durations_that_do_not_match_video_length(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload_approved_asset(client, project["id"])
        confirm_analysis(client, project["id"], asset["id"])
        draft = client.post(
            f"/api/projects/{project['id']}/script",
            json={"content_type": "problem_solution"},
        ).json()["draft"]
        draft["scenes"][0]["duration_seconds"] += 1

        response = client.put(f"/api/projects/{project['id']}/script", json=draft)

        assert response.status_code == 422
        assert "30초" in response.json()["error"]["message"]


def test_project_manager_lists_loads_and_deletes_saved_project_files(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        upload_approved_asset(client, project["id"])
        export_path = settings.data_dir / "exports" / f"{project['id']}.zip"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_bytes(b"saved export")

        projects = client.get("/api/projects")
        assert projects.status_code == 200
        assert projects.json()[0]["id"] == project["id"]

        response = client.delete(f"/api/projects/{project['id']}")

        assert response.status_code == 204
        assert client.get("/api/projects").json() == []
        assert client.get(f"/api/projects/{project['id']}/workspace").status_code == 404
        assert not (settings.data_dir / "projects" / project["id"]).exists()
        assert not export_path.exists()


def test_page_exposes_project_load_and_delete_controls(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert 'id="projectSelect"' in page.text
    assert 'id="loadProjectButton"' in page.text
    assert 'id="deleteProjectButton"' in page.text


def test_asset_delete_removes_media_attribution_and_stale_outputs(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        asset = upload_approved_asset(client, project["id"])
        project_dir = settings.data_dir / "projects" / project["id"]
        asset_path = next((project_dir / "assets").iterdir())
        attribution_path = project_dir / "pexels-attributions.json"
        attribution_path.write_text(
            '[{"asset_id":"%s","creator":"작가","creator_url":"https://www.pexels.com/ko-kr/@creator","media_url":"https://www.pexels.com/ko-kr/photo/1"}]'
            % asset["id"],
            encoding="utf-8",
        )
        (project_dir / "video-vertical.mp4").write_bytes(b"stale video")
        export_path = settings.data_dir / "exports" / f"{project['id']}.zip"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_bytes(b"stale export")

        response = client.delete(f"/api/projects/{project['id']}/assets/{asset['id']}")

        assert response.status_code == 204
        assert not asset_path.exists()
        assert client.get(f"/api/projects/{project['id']}/workspace").json()["assets"] == []
        assert attribution_path.read_text(encoding="utf-8").strip() == "[]"
        assert not (project_dir / "video-vertical.mp4").exists()
        assert not export_path.exists()


def test_asset_delete_clears_saved_scene_selection(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload_approved_asset(client, project["id"])
        confirm_analysis(client, project["id"], asset["id"])
        draft = client.post(
            f"/api/projects/{project['id']}/script",
            json={"content_type": "problem_solution"},
        ).json()["draft"]
        draft["scenes"][0]["asset_id"] = asset["id"]
        assert client.put(f"/api/projects/{project['id']}/script", json=draft).status_code == 200

        response = client.delete(f"/api/projects/{project['id']}/assets/{asset['id']}")

        assert response.status_code == 204
        workspace = client.get(f"/api/projects/{project['id']}/workspace").json()
        assert workspace["script"]["scenes"][0]["asset_id"] is None
