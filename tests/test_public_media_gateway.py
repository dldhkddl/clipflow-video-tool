from fastapi.testclient import TestClient

from clipflow.config import Settings
from clipflow.instagram_publication import PublicVideoSigner
from clipflow.public_media import create_public_media_app


def test_gateway_only_serves_a_valid_signed_video(tmp_path, monkeypatch):
    settings = Settings(base_dir=tmp_path, offline=True, meta_app_secret="secret")
    project_id = "project-1"
    project_dir = settings.data_dir / "projects" / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "video-vertical.mp4").write_bytes(b"video")
    signer = PublicVideoSigner("secret", settings.data_dir / "projects")
    token = signer.issue(project_id, expires_at=2_000)
    monkeypatch.setattr("clipflow.public_media.time.time", lambda: 1_999)

    with TestClient(create_public_media_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/projects").status_code == 404
        assert client.get("/api/meta/status").status_code == 404
        response = client.get(f"/api/public/videos/{token}")

    assert response.status_code == 200
    assert response.content == b"video"
    assert response.headers["content-type"] == "video/mp4"


def test_gateway_rejects_invalid_or_expired_tokens(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=True, meta_app_secret="secret")
    with TestClient(create_public_media_app(settings)) as client:
        assert client.get("/api/public/videos/not-a-token").status_code == 404
