from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time

from fastapi.testclient import TestClient
import imageio_ffmpeg

import clipflow.app as app_module
from clipflow.app import create_app
from clipflow.config import Settings
from clipflow.providers.gemini import GeminiError
from test_workspace_api import create_project


def valid_mp4_bytes(tmp_path, color="red"):
    output = tmp_path / f"valid-{color}.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x284:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return output.read_bytes()


def valid_webm_bytes(tmp_path):
    output = tmp_path / "valid.webm"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x284:d=0.2",
            "-c:v",
            "libvpx-vp9",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return output.read_bytes()


def test_offline_social_copy_uses_prompt_subject_and_korean_hashtags(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/social-copy/generate",
            json={
                "prompt": "오늘 처음 면접을 본 검은색 시바견 줄라이. 과연 합격할 수 있을까?"
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert "줄라이" in payload["description"]
    assert payload["description"].startswith("오늘 면접 본 줄라이")
    assert 5 <= len(payload["hashtags"]) <= 12
    assert all(tag.startswith("#") for tag in payload["hashtags"])


def test_social_copy_can_be_edited_saved_and_reloaded(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        saved = client.put(
            f"/api/projects/{project['id']}/social-copy",
            json={
                "description": "오늘 면접 본 줄라이, 과연 어떻게 될까요? 결과는 영상에서 확인해 보세요.",
                "hashtags": ["#줄라이", "#시바견", "#강아지릴스", "#면접", "#샘플업체"],
            },
        )
        loaded = client.get(f"/api/projects/{project['id']}/social-copy")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()
    text = (
        settings.data_dir / "projects" / project["id"] / "인스타그램-문구.txt"
    ).read_text(encoding="utf-8")
    assert "오늘 면접 본 줄라이" in text
    assert "#줄라이 #시바견" in text


def test_missing_social_copy_returns_empty_editable_value(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        response = client.get(f"/api/projects/{project['id']}/social-copy")

    assert response.status_code == 200
    assert response.json() == {"description": "", "hashtags": []}


def test_uploaded_mp4_becomes_final_video_and_resets_instagram_publication(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    video_bytes = valid_mp4_bytes(tmp_path)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "instagram-publication.json").write_text(
            '{"project_id":"%s","status":"published","media_id":"old"}'
            % project["id"],
            encoding="utf-8",
        )
        client.put(
            f"/api/projects/{project['id']}/social-copy",
            json={"description": "이전 영상 설명", "hashtags": ["#이전영상"]},
        )

        response = client.post(
            f"/api/projects/{project['id']}/videos/upload",
            files={"file": ("내영상.mp4", video_bytes, "video/mp4")},
        )
        workspace = client.get(f"/api/projects/{project['id']}/workspace")
        publication = client.get(
            f"/api/projects/{project['id']}/instagram/status"
        )
        social_copy = client.get(f"/api/projects/{project['id']}/social-copy")

    assert response.status_code == 201
    assert response.json()["asset"]["rights_approved"] is True
    assert response.json()["video_url"].endswith("/media/video")
    assert (project_dir / "video-vertical.mp4").read_bytes() == video_bytes
    assert workspace.json()["video_url"].endswith("/media/video")
    assert publication.json()["status"] == "not_prepared"
    assert social_copy.json() == {"description": "", "hashtags": []}


def test_video_upload_rejects_non_mp4_without_replacing_final_video(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        final_video = project_dir / "video-vertical.mp4"
        final_video.write_bytes(b"keep-me")

        response = client.post(
            f"/api/projects/{project['id']}/videos/upload",
            files={"file": ("사진.png", b"not-a-video", "image/png")},
        )

    assert response.status_code == 422
    assert "MP4" in response.json()["error"]["message"]
    assert final_video.read_bytes() == b"keep-me"


def test_video_upload_rejects_corrupted_mp4_without_replacing_final_video(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        final_video = project_dir / "video-vertical.mp4"
        final_video.write_bytes(b"keep-me")

        response = client.post(
            f"/api/projects/{project['id']}/videos/upload",
            files={"file": ("손상.mp4", b"not-a-real-mp4", "video/mp4")},
        )

    assert response.status_code == 422
    assert "손상" in response.json()["error"]["message"]
    assert final_video.read_bytes() == b"keep-me"


def test_video_upload_rejects_webm_renamed_as_mp4(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    webm = valid_webm_bytes(tmp_path)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/videos/upload",
            files={"file": ("위장영상.mp4", webm, "video/mp4")},
        )

    assert response.status_code == 422
    assert "MP4 형식" in response.json()["error"]["message"]


def test_same_project_video_uploads_are_serialized_and_leave_no_temp_files(
    tmp_path, monkeypatch
):
    settings = Settings(base_dir=tmp_path, offline=True)
    red = valid_mp4_bytes(tmp_path, "red")
    blue = valid_mp4_bytes(tmp_path, "blue")
    real_inspect = app_module.inspect_video
    guard = threading.Lock()
    active = 0
    max_active = 0

    def slow_inspect(path):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.15)
            return real_inspect(path)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(app_module, "inspect_video", slow_inspect)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)

        def upload(name, data):
            return client.post(
                f"/api/projects/{project['id']}/videos/upload",
                files={"file": (name, data, "video/mp4")},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda item: upload(*item),
                    [("빨강.mp4", red), ("파랑.mp4", blue)],
                )
            )

    project_dir = settings.data_dir / "projects" / project["id"]
    assert [response.status_code for response in responses] == [201, 201]
    assert max_active == 1
    assert (project_dir / "video-vertical.mp4").read_bytes() in {red, blue}
    assert list(project_dir.glob(".video-upload-*.mp4")) == []


def test_online_social_copy_analyzes_final_video_instead_of_reference_image(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)

    class FakeVideoClient:
        def __init__(self):
            self.received = None

        def generate_json_from_file(self, **kwargs):
            self.received = kwargs
            self.file_bytes = kwargs["file_path"].read_bytes()
            return {
                "description": "영상 속 줄라이가 첫 출근부터 사고를 쳤어요. 과연 무사히 수습할 수 있을까요?",
                "hashtags": ["#줄라이", "#강아지릴스", "#첫출근"],
            }

    fake = FakeVideoClient()
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        final_video = project_dir / "video-vertical.mp4"
        final_video.write_bytes(b"video-with-dialogue")
        client.app.state.services.social_copy.client = fake

        response = client.post(
            f"/api/projects/{project['id']}/social-copy/generate",
            json={"prompt": ""},
        )

    assert response.status_code == 201
    assert response.json()["description"].startswith("영상 속 줄라이")
    assert fake.file_bytes == b"video-with-dialogue"
    assert fake.received["content_type"] == "video/mp4"
    assert "음성" in fake.received["system_prompt"]


def test_gemini_video_analysis_failure_is_retryable_service_error(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)

    class FailingVideoClient:
        def generate_json_from_file(self, **_kwargs):
            raise GeminiError("Gemini 서버에 연결할 수 없습니다.")

    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "video-vertical.mp4").write_bytes(b"video")
        client.app.state.services.social_copy.client = FailingVideoClient()

        response = client.post(
            f"/api/projects/{project['id']}/social-copy/generate",
            json={"prompt": ""},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "GEMINI_UNAVAILABLE"
    assert "다시" in response.json()["error"]["recovery"]


def test_invalid_gemini_social_copy_is_retryable_service_error(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)

    class InvalidVideoClient:
        def generate_json_from_file(self, **_kwargs):
            return {"description": [], "hashtags": "잘못된 형식"}

    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "video-vertical.mp4").write_bytes(b"video")
        client.app.state.services.social_copy.client = InvalidVideoClient()
        response = client.post(
            f"/api/projects/{project['id']}/social-copy/generate",
            json={"prompt": ""},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "GEMINI_UNAVAILABLE"


def test_social_copy_discards_result_when_video_changes_during_analysis(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        final_video = project_dir / "video-vertical.mp4"
        final_video.write_bytes(b"video-a")

        class MutatingVideoClient:
            def generate_json_from_file(self, **_kwargs):
                final_video.write_bytes(b"video-b")
                return {
                    "description": "이전 영상 A의 설명",
                    "hashtags": ["#이전영상"],
                }

        client.app.state.services.social_copy.client = MutatingVideoClient()
        response = client.post(
            f"/api/projects/{project['id']}/social-copy/generate",
            json={"prompt": ""},
        )
        saved = client.get(f"/api/projects/{project['id']}/social-copy")

    assert response.status_code == 503
    assert "변경" in response.json()["error"]["message"]
    assert saved.json() == {"description": "", "hashtags": []}


def test_video_upload_waits_for_same_project_social_copy_analysis(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    video_a = valid_mp4_bytes(tmp_path, "yellow")
    video_b = valid_mp4_bytes(tmp_path, "purple")
    analysis_started = threading.Event()
    release_analysis = threading.Event()
    upload_finished = threading.Event()

    class BlockingVideoClient:
        def generate_json_from_file(self, **_kwargs):
            analysis_started.set()
            assert release_analysis.wait(10)
            return {"description": "영상 A 설명", "hashtags": ["#영상A"]}

    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        initial = client.post(
            f"/api/projects/{project['id']}/videos/upload",
            files={"file": ("영상A.mp4", video_a, "video/mp4")},
        )
        assert initial.status_code == 201
        client.app.state.services.social_copy.client = BlockingVideoClient()

        def analyze():
            return client.post(
                f"/api/projects/{project['id']}/social-copy/generate",
                json={"prompt": ""},
            )

        def upload_new():
            try:
                return client.post(
                    f"/api/projects/{project['id']}/videos/upload",
                    files={"file": ("영상B.mp4", video_b, "video/mp4")},
                )
            finally:
                upload_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            analysis_future = executor.submit(analyze)
            assert analysis_started.wait(5)
            upload_future = executor.submit(upload_new)
            assert not upload_finished.wait(1)
            release_analysis.set()
            analysis_response = analysis_future.result(timeout=10)
            upload_response = upload_future.result(timeout=10)

        saved = client.get(f"/api/projects/{project['id']}/social-copy")

    assert analysis_response.status_code == 201
    assert upload_response.status_code == 201
    assert saved.json() == {"description": "", "hashtags": []}


def test_project_delete_waits_for_same_project_video_upload(tmp_path, monkeypatch):
    settings = Settings(base_dir=tmp_path, offline=True)
    video = valid_mp4_bytes(tmp_path, "orange")
    inspection_started = threading.Event()
    release_inspection = threading.Event()
    delete_finished = threading.Event()
    original_inspect = app_module.inspect_video

    def blocking_inspect(path):
        inspection_started.set()
        assert release_inspection.wait(10)
        return original_inspect(path)

    monkeypatch.setattr(app_module, "inspect_video", blocking_inspect)
    with TestClient(create_app(settings)) as client:
        project = create_project(client)

        def upload():
            return client.post(
                f"/api/projects/{project['id']}/videos/upload",
                files={"file": ("삭제경쟁.mp4", video, "video/mp4")},
            )

        def delete():
            try:
                return client.delete(f"/api/projects/{project['id']}")
            finally:
                delete_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            upload_future = executor.submit(upload)
            assert inspection_started.wait(5)
            delete_future = executor.submit(delete)
            assert not delete_finished.wait(1)
            release_inspection.set()
            upload_response = upload_future.result(timeout=10)
            delete_response = delete_future.result(timeout=10)

        missing = client.get(f"/api/projects/{project['id']}/workspace")
        project_dir = settings.data_dir / "projects" / project["id"]

    assert upload_response.status_code == 201
    assert delete_response.status_code == 204
    assert missing.status_code == 404
    assert not project_dir.exists()


def test_project_delete_waits_for_same_project_video_analysis(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True)
    analysis_started = threading.Event()
    release_analysis = threading.Event()
    delete_finished = threading.Event()

    class BlockingVideoClient:
        def generate_json_from_file(self, **_kwargs):
            analysis_started.set()
            assert release_analysis.wait(10)
            return {"description": "삭제 전 분석 결과", "hashtags": ["#삭제전"]}

    with TestClient(create_app(settings)) as client:
        project = create_project(client)
        project_dir = settings.data_dir / "projects" / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "video-vertical.mp4").write_bytes(b"analysis-video")
        client.app.state.services.social_copy.client = BlockingVideoClient()

        def analyze():
            return client.post(
                f"/api/projects/{project['id']}/social-copy/generate",
                json={"prompt": ""},
            )

        def delete():
            try:
                return client.delete(f"/api/projects/{project['id']}")
            finally:
                delete_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            analysis_future = executor.submit(analyze)
            assert analysis_started.wait(5)
            delete_future = executor.submit(delete)
            assert not delete_finished.wait(1)
            release_analysis.set()
            analysis_response = analysis_future.result(timeout=10)
            delete_response = delete_future.result(timeout=10)

        missing = client.get(f"/api/projects/{project['id']}/workspace")

    assert analysis_response.status_code == 201
    assert delete_response.status_code == 204
    assert missing.status_code == 404
    assert not project_dir.exists()
