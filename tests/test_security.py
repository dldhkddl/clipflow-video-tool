from fastapi.testclient import TestClient

from clipflow.app import create_app
from clipflow.config import Settings


def create_project(client):
    return client.post(
        "/api/projects",
        json={
            "name": "보안 테스트",
            "affiliate_url": "https://link.coupang.com/a/example",
            "product_name": "상품",
            "category": "생활",
            "features": ["특징"],
            "target_seconds": 30,
            "platforms": ["youtube"],
        },
    ).json()


def test_rejects_path_traversal_upload_with_recovery_message(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/assets",
            files={"file": ("../secret.png", b"not-an-image", "image/png")},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_server_host_is_loopback_and_secret_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-secret")
    settings = Settings.load(tmp_path)
    assert settings.host == "127.0.0.1"
    assert "sentinel-secret" not in repr(settings)
