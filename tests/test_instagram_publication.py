import time

from fastapi.testclient import TestClient
import pytest

from clipflow.app import create_app
from clipflow.config import Settings
from clipflow.instagram_publication import (
    InstagramPublication,
    InstagramPublicationRepository,
    PublicVideoSigner,
)
from clipflow.models import AFFILIATE_DISCLOSURE


class FakeMeta:
    configured = True

    def __init__(self):
        self.created = []
        self.published = []
        self.remote_status = "IN_PROGRESS"

    def status(self):
        return {
            "configured": True,
            "connected": True,
            "id": "17841400000000000",
            "username": "fruit_born",
            "account_type": "MEDIA_CREATOR",
        }

    def create_reel(self, video_url, caption):
        self.created.append((video_url, caption))
        return "container-1"

    def container_status(self, container_id):
        return {
            "id": container_id,
            "status_code": self.remote_status,
            "status": self.remote_status.title(),
        }

    def publish(self, container_id):
        self.published.append(container_id)
        return "media-1"


def create_project(client):
    return client.post(
        "/api/projects",
        json={
            "name": "인스타 테스트",
            "affiliate_url": "https://example.com/fruit",
            "product_name": "제철 복숭아",
            "category": "과일",
            "features": ["당일 선별"],
            "target_seconds": 30,
            "platforms": ["instagram"],
        },
    ).json()


def complete_project(settings, client, project_id):
    project_dir = settings.data_dir / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "video-vertical.mp4").write_bytes(b"fake-mp4")
    response = client.put(
        f"/api/projects/{project_id}/review",
        json={
            "product_facts": True,
            "asset_rights": True,
            "honest_experience": True,
            "affiliate_disclosure": True,
        },
    )
    assert response.status_code == 200


def test_public_video_signer_accepts_valid_token_and_rejects_tampering(tmp_path):
    signer = PublicVideoSigner("app-secret", tmp_path)
    token = signer.issue("project-1", expires_at=2_000)

    assert signer.verify(token, now=1_999) == "project-1"
    with pytest.raises(ValueError):
        signer.verify(f"{token}x", now=1_999)
    with pytest.raises(ValueError):
        signer.verify(token, now=2_001)


def test_publication_repository_round_trip(tmp_path):
    repository = InstagramPublicationRepository(tmp_path)
    saved = repository.save(
        "project-1",
        InstagramPublication(project_id="project-1", container_id="container-1"),
    )

    assert saved.container_id == "container-1"
    assert repository.load("project-1").container_id == "container-1"


def test_prepare_status_and_explicit_publish_flow(tmp_path):
    settings = Settings(
        base_dir=tmp_path,
        offline=True,
        meta_instagram_access_token="token",
        meta_instagram_user_id="17841400000000000",
        meta_app_secret="app-secret",
        meta_public_base_url="https://public.example",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        fake = FakeMeta()
        app.state.services.meta = fake
        project = create_project(client)
        complete_project(settings, client, project["id"])

        assert client.get("/api/meta/status").json()["username"] == "fruit_born"

        prepared = client.post(
            f"/api/projects/{project['id']}/instagram/prepare"
        )
        assert prepared.status_code == 201
        assert prepared.json()["status"] == "in_progress"
        video_url, caption = fake.created[0]
        assert video_url.startswith("https://public.example/api/public/videos/")
        assert AFFILIATE_DISCLOSURE in caption

        token = video_url.rsplit("/", 1)[-1]
        media = client.get(f"/api/public/videos/{token}")
        assert media.status_code == 200
        assert media.content == b"fake-mp4"

        waiting = client.get(
            f"/api/projects/{project['id']}/instagram/status"
        )
        assert waiting.json()["status"] == "in_progress"

        fake.remote_status = "FINISHED"
        finished = client.get(
            f"/api/projects/{project['id']}/instagram/status"
        )
        assert finished.json()["status"] == "finished"

        published = client.post(
            f"/api/projects/{project['id']}/instagram/publish"
        )
        assert published.status_code == 201
        assert published.json()["media_id"] == "media-1"
        assert fake.published == ["container-1"]

        duplicate = client.post(
            f"/api/projects/{project['id']}/instagram/publish"
        )
        assert duplicate.status_code == 409


def test_prepare_requires_review_video_and_public_url(tmp_path):
    settings = Settings(
        base_dir=tmp_path,
        offline=True,
        meta_instagram_access_token="token",
        meta_instagram_user_id="17841400000000000",
        meta_app_secret="app-secret",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.services.meta = FakeMeta()
        project = create_project(client)

        missing_review = client.post(
            f"/api/projects/{project['id']}/instagram/prepare"
        )
        assert missing_review.status_code == 409

        complete_project(settings, client, project["id"])
        missing_url = client.post(
            f"/api/projects/{project['id']}/instagram/prepare"
        )
        assert missing_url.status_code == 409
        assert "META_PUBLIC_BASE_URL" in missing_url.json()["detail"]


def test_prepare_uses_saved_reel_description_and_hashtags(tmp_path):
    settings = Settings(
        base_dir=tmp_path,
        offline=True,
        meta_instagram_access_token="token",
        meta_instagram_user_id="17841400000000000",
        meta_app_secret="app-secret",
        meta_public_base_url="https://public.example",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        fake = FakeMeta()
        app.state.services.meta = fake
        project = create_project(client)
        complete_project(settings, client, project["id"])
        saved_caption = (
            "오늘 줄라이가 사과를 구경했어요.\n"
            "씨와 심지는 빼고 소량만 주세요.\n\n"
            "#샘플업체 #줄라이 #강아지과일"
        )
        project_dir = settings.data_dir / "projects" / project["id"]
        (project_dir / "인스타그램-문구.txt").write_text(
            saved_caption, encoding="utf-8"
        )

        prepared = client.post(f"/api/projects/{project['id']}/instagram/prepare")

        assert prepared.status_code == 201
        assert fake.created[0][1] == saved_caption
