from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import create_app
from clipflow.config import Settings


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 80), "peachpuff").save(buffer, format="PNG")
    return buffer.getvalue()


def create_project(client: TestClient) -> dict:
    return client.post(
        "/api/projects",
        json={
            "name": "복숭아 홍보",
            "affiliate_url": "https://link.coupang.com/a/example",
            "product_name": "복숭아",
            "category": "농수산물",
            "features": ["과즙이 풍부함", "향이 진함"],
            "target_seconds": 30,
            "platforms": ["youtube", "instagram"],
        },
    ).json()


def upload(client: TestClient, project_id: str, *, approve: bool) -> dict:
    asset = client.post(
        f"/api/projects/{project_id}/assets",
        files={"file": ("peach.png", png_bytes(), "image/png")},
    ).json()
    if approve:
        client.put(
            f"/api/projects/{project_id}/assets/{asset['id']}/rights",
            json={"approved": True},
        )
    return asset


def test_generate_edit_and_restore_product_analysis(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload(client, project["id"], approve=True)

        response = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={"asset_ids": [asset["id"]]},
        )
        assert response.status_code == 201
        analysis = response.json()
        assert analysis["mode"] == "demo"
        assert analysis["confirmed"] is False

        analysis["confirmed"] = True
        analysis["taste_notes"] = [
            {"text": "상큼하고 달콤한 맛", "origin": "user"}
        ]
        saved = client.put(
            f"/api/projects/{project['id']}/analysis",
            json=analysis,
        )
        assert saved.status_code == 200

        workspace = client.get(f"/api/projects/{project['id']}/workspace").json()
        assert workspace["analysis"]["taste_notes"][0]["text"] == "상큼하고 달콤한 맛"
        assert workspace["analysis"]["confirmed"] is True
        assert workspace["max_step"] == 3


def test_analysis_rejects_unapproved_asset(tmp_path):
    with TestClient(create_app(Settings(base_dir=tmp_path, offline=True))) as client:
        project = create_project(client)
        asset = upload(client, project["id"], approve=False)

        response = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={"asset_ids": [asset["id"]]},
        )

        assert response.status_code == 422
        assert "권리" in response.json()["error"]["message"]
