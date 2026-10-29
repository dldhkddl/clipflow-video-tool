import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clipflow.app import Services, create_app
from clipflow.assets import AssetService
from clipflow.config import Settings
from clipflow.db import Database
from clipflow.models import (
    AFFILIATE_DISCLOSURE,
    AnalysisItem,
    ProductAnalysis,
    ProjectCreate,
    ScriptRequest,
)
from clipflow.product_analysis import ProductAnalysisService
from clipflow.projects import ProjectRepository
import clipflow.providers.gemini as gemini_module
from clipflow.providers.gemini import GeminiClient, GeminiError, GeminiScriptProvider


def gemini_response(value: dict) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(value, ensure_ascii=False)}]}}
        ]
    }


def create_project(database: Database):
    return ProjectRepository(database).create(
        ProjectCreate(
            name="복숭아 광고",
            affiliate_url="https://link.coupang.com/a/example",
            product_name="복숭아",
            category="농수산물",
            features=["과즙이 풍부함", "향이 진함"],
            target_seconds=30,
            platforms={"youtube", "instagram"},
        )
    )


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "peachpuff").save(buffer, format="PNG")
    return buffer.getvalue()


def test_gemini_default_uses_current_stable_flash_model():
    captured = {}

    def post(url, headers, payload):
        captured["url"] = url
        return gemini_response({"status": "ok"})

    result = GeminiClient("secret-key", json_post=post).generate_json(
        prompt="테스트",
        schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
        system_prompt="테스트",
    )

    assert result == {"status": "ok"}
    assert captured["url"].endswith("/gemini-3.6-flash:generateContent")


@pytest.mark.asyncio
async def test_gemini_script_uses_confirmed_analysis_and_returns_editable_draft():
    captured = {}
    draft = {
        "hook": "복숭아 고를 때 이것부터 보세요",
        "selection_criteria": ["표면 상태", "과즙"],
        "pros": ["달콤한 향"],
        "cons": ["보관 상태를 확인하세요"],
        "cta": "자세한 구성은 링크에서 확인하세요",
        "disclosure": "모델이 바꾼 고지",
        "scenes": [
            {
                "order": index,
                "narration": f"장면 {index} 내레이션",
                "on_screen_text": f"장면 {index}",
                "duration_seconds": 6,
            }
            for index in range(1, 6)
        ],
        "fact_check_notes": ["원산지는 상품 페이지 확인"],
    }

    def post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return gemini_response(draft)

    analysis = ProductAnalysis(
        product_name="복숭아",
        category="농수산물",
        visible_features=[AnalysisItem(text="붉은빛이 도는 껍질", origin="visual")],
        package_text=[],
        taste_notes=[AnalysisItem(text="달콤한 향이 기대되는 비주얼", origin="ai_promotion")],
        use_cases=[AnalysisItem(text="시원하게 즐기기", origin="ai_promotion")],
        promotion_points=[AnalysisItem(text="과즙이 풍부함", origin="user")],
        visual_direction=[],
        source_asset_ids=["asset-1"],
        mode="vision",
        confirmed=True,
    )
    request = ScriptRequest(
        product_name="복숭아",
        category="농수산물",
        features=["과즙이 풍부함"],
        target_seconds=30,
        content_type="problem_solution",
        analysis=analysis,
        tone="friend",
    )

    result = await GeminiScriptProvider(
        "secret-key", model="gemini-2.5-flash", json_post=post
    ).generate(request)

    assert result.hook == "복숭아 고를 때 이것부터 보세요"
    assert result.disclosure == AFFILIATE_DISCLOSURE
    assert sum(scene.duration_seconds for scene in result.scenes) == 30
    assert captured["headers"]["x-goog-api-key"] == "secret-key"
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    prompt = captured["payload"]["contents"][0]["parts"][0]["text"]
    assert "붉은빛이 도는 껍질" in prompt
    assert "과즙이 풍부함" in prompt


def test_gemini_product_analysis_sends_approved_image_and_restores_internal_fields(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    project = create_project(database)
    assets = AssetService(database, tmp_path / "projects")
    asset = assets.save(project.id, "peach.png", "image/png", png_bytes())
    asset = assets.approve_rights(asset.id, True)
    captured = {}
    generated = {
        "product_name": "모델이 바꾼 상품명",
        "category": "모델이 바꾼 카테고리",
        "visible_features": [{"text": "붉은빛 껍질", "origin": "visual"}],
        "package_text": [],
        "taste_notes": [{"text": "달콤한 맛이 기대됨", "origin": "ai_promotion"}],
        "use_cases": [{"text": "간식으로 즐기기", "origin": "ai_promotion"}],
        "promotion_points": [{"text": "과즙이 풍부함", "origin": "user"}],
        "visual_direction": [],
        "source_asset_ids": ["wrong-id"],
        "mode": "demo",
        "confirmed": True,
    }

    def post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return gemini_response(generated)

    service = ProductAnalysisService(
        database,
        offline=False,
        gemini_api_key="secret-key",
        gemini_model="gemini-2.5-flash",
        gemini_json_post=post,
    )

    result = service.generate(project, [asset])

    parts = captured["payload"]["contents"][0]["parts"]
    assert any(part.get("inline_data", {}).get("mime_type") == "image/png" for part in parts)
    assert result.product_name == "복숭아"
    assert result.category == "농수산물"
    assert result.source_asset_ids == [asset.id]
    assert result.mode == "vision"
    assert result.confirmed is False


def test_services_prefer_gemini_for_analysis_and_script_when_key_is_set(tmp_path):
    services = Services(
        Settings(
            base_dir=tmp_path,
            offline=False,
            openai_api_key="openai-key",
            gemini_api_key="gemini-key",
        )
    )

    assert isinstance(services.script_provider, GeminiScriptProvider)
    assert services.product_analysis.gemini_client is not None


def test_page_labels_gemini_connection_when_gemini_key_is_set(tmp_path):
    settings = Settings(base_dir=tmp_path, offline=False, gemini_api_key="gemini-key")

    with TestClient(create_app(settings)) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "Gemini 연결 모드" in page.text
    assert "완성 영상의 화면과 음성·대사를 분석해 생성합니다." in page.text


def test_gemini_file_analysis_waits_for_video_and_removes_remote_file(tmp_path):
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")
    states = iter(
        [
            {"name": "files/episode", "uri": "https://files/episode", "state": "PROCESSING"},
            {"name": "files/episode", "uri": "https://files/episode", "state": "ACTIVE"},
        ]
    )
    captured = {}
    deleted = []

    def upload(file_path, content_type):
        assert file_path == video
        assert content_type == "video/mp4"
        return next(states)

    def get_file(name):
        assert name == "files/episode"
        return next(states)

    def delete_file(name):
        deleted.append(name)

    def post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return gemini_response({"description": "영상 분석 완료", "hashtags": ["#영상"]})

    client = GeminiClient(
        "secret-key",
        "gemini-2.5-flash",
        json_post=post,
        file_upload=upload,
        file_get=get_file,
        file_delete=delete_file,
        sleep=lambda _seconds: None,
    )
    result = client.generate_json_from_file(
        prompt="영상의 화면과 대사를 분석해 주세요.",
        schema={"type": "object"},
        system_prompt="한국어 게시 문구 작성",
        file_path=video,
        content_type="video/mp4",
    )

    assert result == {"description": "영상 분석 완료", "hashtags": ["#영상"]}
    parts = captured["payload"]["contents"][0]["parts"]
    assert parts[0] == {"text": "영상의 화면과 대사를 분석해 주세요."}
    assert parts[1] == {
        "file_data": {
            "mime_type": "video/mp4",
            "file_uri": "https://files/episode",
        }
    }
    assert deleted == ["files/episode"]


def test_gemini_file_analysis_reports_processing_failure(tmp_path):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"video")
    client = GeminiClient(
        "secret-key",
        file_upload=lambda _path, _type: {
            "name": "files/broken",
            "uri": "https://files/broken",
            "state": "FAILED",
        },
        file_get=lambda _name: {},
        file_delete=lambda _name: None,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ValueError, match="영상 파일을 처리하지 못했습니다"):
        client.generate_json_from_file(
            prompt="분석",
            schema={"type": "object"},
            system_prompt="한국어",
            file_path=video,
            content_type="video/mp4",
        )


def test_gemini_generation_allows_long_video_analysis_and_reports_timeout(monkeypatch):
    captured = {}

    def timeout_urlopen(_request, timeout):
        captured["timeout"] = timeout
        raise TimeoutError("read timed out")

    monkeypatch.setattr(gemini_module, "urlopen", timeout_urlopen)

    with pytest.raises(GeminiError, match="분석 시간이 초과"):
        GeminiClient._default_json_post(
            "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
            {"Content-Type": "application/json"},
            {"contents": []},
        )

    assert captured["timeout"] == 180
