from fastapi.testclient import TestClient

from clipflow.app import create_app
from clipflow.config import Settings
from test_workspace_api import create_project


YOUTUBE_SHORT = "https://www.youtube.com/shorts/iIWYh5r3N0E"


def test_youtube_short_is_saved_as_embedded_reference(tmp_path):
    app = create_app(Settings(base_dir=tmp_path, offline=True))
    with TestClient(app) as client:
        project = create_project(client)

        response = client.post(
            f"/api/projects/{project['id']}/references",
            json={"url": YOUTUBE_SHORT},
        )

        assert response.status_code == 201
        assert response.json() == {
            "id": "iIWYh5r3N0E",
            "source": "youtube",
            "original_url": YOUTUBE_SHORT,
            "embed_url": "https://www.youtube-nocookie.com/embed/iIWYh5r3N0E",
            "renderable": False,
        }
        workspace = client.get(
            f"/api/projects/{project['id']}/workspace"
        ).json()
        assert workspace["references"] == [response.json()]
        assert workspace["max_step"] == 2


def test_non_youtube_link_is_rejected_without_saving(tmp_path):
    app = create_app(Settings(base_dir=tmp_path, offline=True))
    with TestClient(app) as client:
        project = create_project(client)

        response = client.post(
            f"/api/projects/{project['id']}/references",
            json={"url": "https://example.com/video"},
        )

        assert response.status_code == 422
        workspace = client.get(
            f"/api/projects/{project['id']}/workspace"
        ).json()
        assert workspace["references"] == []
