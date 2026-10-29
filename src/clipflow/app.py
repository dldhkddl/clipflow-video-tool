import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import shutil
import time
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import Literal

from .ai_assets import AIAssetService, CostConfirmationRequired
from .assets import MAX_FILE_BYTES, AssetNotFound, AssetService, RightsNotApproved
from .config import Settings
from .daily import (
    DailyProductCreate,
    DailyProductUpdate,
    DailyQueueRepository,
    DailyRunInProgress,
    DailyRunner,
    build_daily_item,
    daily_run_lock,
)
from .db import Database
from .export import ExportService, FinalVideoMissing, ReviewIncomplete
from .media.render import RenderRequest, RenderService
from .media.inspect import inspect_video
from .meta_instagram import MetaInstagramClient, MetaInstagramError
from .instagram_publication import (
    InstagramPublication,
    InstagramPublicationRepository,
    PublicVideoSigner,
    utc_now as publication_utc_now,
)
from .models import (
    AFFILIATE_DISCLOSURE,
    AI_VOICE_DISCLOSURE,
    ProductAnalysis,
    ProjectCreate,
    ReviewState,
    ScriptDraft,
    ScriptRequest,
)
from .pexels import PexelsNotConfigured, PexelsService
from .product_analysis import ProductAnalysisService
from .projects import ProjectNotFound, ProjectRepository
from .providers.fake import FakeScriptProvider, FakeSpeechProvider
from .providers.gemini import GeminiError, GeminiScriptProvider
from .providers.gemini_speech import GeminiSpeechProvider
from .providers.higgsfield import VideoGenerationOptions
from .providers.higgsfield import HiggsfieldError
from .providers.openai_script import OpenAIScriptProvider
from .providers.openai_speech import OpenAISpeechProvider
from .references import ReferenceService
from .reel_automation import (
    ReelAutomationRepository,
    ReelDailyRunner,
    ReelPlanner,
    build_reel_item,
)
from .social_copy import SocialCopy, SocialCopyRepository, SocialCopyService
from .workflow import (
    AutoBuildFailed,
    AutoBuildOptions,
    latest_script,
    run_auto_build,
    save_script_version,
)
from .video_profile import VideoProfile, VideoProfileRepository


class RightsUpdate(BaseModel):
    approved: bool


class ScriptOptions(BaseModel):
    content_type: str = "problem_solution"
    tone: Literal["friend", "shopping", "calm_review"] = "friend"


class RenderOptions(BaseModel):
    profile: str = "preview"


class AIAssetRequest(BaseModel):
    kind: Literal["image", "video"]
    prompt: str = Field(min_length=3, max_length=5000)
    start_asset_id: str | None = None
    confirmed_cost: bool = False
    use_video_profile: bool = True
    use_profile_reference: bool = True
    video_model: Literal["seedance_2_0"] = "seedance_2_0"
    video_duration: Literal[5, 8] = 8
    video_aspect_ratio: Literal["auto", "9:16", "16:9"] = "auto"
    video_resolution: Literal["720p", "1080p"] = "720p"
    video_bitrate_mode: Literal["standard", "high"] = "high"
    video_generate_audio: bool = True

    def video_options(self) -> VideoGenerationOptions:
        return VideoGenerationOptions(
            model=self.video_model,
            duration=self.video_duration,
            aspect_ratio=self.video_aspect_ratio,
            resolution=self.video_resolution,
            bitrate_mode=self.video_bitrate_mode,
            generate_audio=self.video_generate_audio,
        )


class ReferenceRequest(BaseModel):
    url: str = Field(min_length=10, max_length=500)


class AnalysisRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=20)


class PexelsImportRequest(BaseModel):
    media_type: Literal["image", "video"]
    pexels_id: int = Field(gt=0)


class SocialCopyGenerateRequest(BaseModel):
    prompt: str = Field(default="", max_length=5000)
    image_asset_id: str | None = None


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.initialize()
        self.projects = ProjectRepository(self.db)
        self.daily_queue = DailyQueueRepository(self.db)
        self.daily_builder = build_daily_item
        self.reel_automation = ReelAutomationRepository(self.db)
        self.reel_planner = ReelPlanner(settings.brand_name, settings.dog_name)
        self.reel_builder = build_reel_item
        self.assets = AssetService(self.db, settings.data_dir / "projects")
        pexels_key = (
            settings.pexels_api_key.get_secret_value()
            if settings.pexels_api_key is not None
            else None
        )
        self.pexels = PexelsService(
            pexels_key,
            self.assets,
            settings.data_dir / "projects",
        )
        self.exports = ExportService(
            self.db,
            self.projects,
            settings.data_dir / "projects",
            settings.data_dir / "exports",
        )
        self.renderer = RenderService()
        self.ai_assets = AIAssetService(self.assets, settings.offline)
        self.video_profile = VideoProfileRepository(
            settings.data_dir / "video-profile.json"
        )
        self.references = ReferenceService(settings.data_dir / "projects")
        meta_token = (
            settings.meta_instagram_access_token.get_secret_value()
            if settings.meta_instagram_access_token is not None
            else None
        )
        self.meta = MetaInstagramClient(
            meta_token,
            settings.meta_instagram_user_id,
        )
        self.instagram_publications = InstagramPublicationRepository(
            settings.data_dir / "projects"
        )
        meta_secret = (
            settings.meta_app_secret.get_secret_value()
            if settings.meta_app_secret is not None
            else None
        )
        self.public_video_signer = (
            PublicVideoSigner(meta_secret, settings.data_dir / "projects")
            if meta_secret
            else None
        )
        analysis_key = (
            settings.openai_api_key.get_secret_value()
            if settings.openai_api_key is not None
            else None
        )
        gemini_key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else None
        )
        self.social_copy = SocialCopyService(
            SocialCopyRepository(settings.data_dir / "projects"),
            offline=settings.offline,
            gemini_api_key=gemini_key,
            gemini_model=settings.gemini_model,
        )
        self.product_analysis = ProductAnalysisService(
            self.db,
            offline=settings.offline,
            api_key=analysis_key,
            model=settings.script_model,
            gemini_api_key=gemini_key,
            gemini_model=settings.gemini_model,
        )
        if settings.offline:
            self.script_provider = FakeScriptProvider()
            self.speech_provider = FakeSpeechProvider(seconds=30)
            self.speech_voice = settings.tts_voice
        else:
            if gemini_key:
                self.script_provider = GeminiScriptProvider(gemini_key, settings.gemini_model)
                self.speech_provider = GeminiSpeechProvider(
                    gemini_key, settings.gemini_tts_model
                )
                self.speech_voice = settings.gemini_tts_voice
            elif analysis_key:
                self.script_provider = OpenAIScriptProvider(analysis_key, settings.script_model)
                self.speech_provider = OpenAISpeechProvider(
                    analysis_key, settings.tts_model
                )
                self.speech_voice = settings.tts_voice
            else:
                self.script_provider = None
                self.speech_provider = None
                self.speech_voice = settings.tts_voice


def create_app(settings: Settings | None = None) -> FastAPI:
    root = Path(__file__).resolve().parents[2]
    app_settings = settings or Settings.load(root)
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = Services(app_settings)
        yield

    app = FastAPI(title="ClipFlow AI", lifespan=lifespan)
    video_upload_locks: dict[str, asyncio.Lock] = {}
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    def services(request: Request) -> Services:
        return request.app.state.services

    def saved_instagram_text(service: Services, project_id: str | None) -> str:
        if not project_id:
            return ""
        project_root = (service.settings.data_dir / "projects").resolve()
        project_dir = (project_root / project_id).resolve()
        caption_path = project_dir / "인스타그램-문구.txt"
        if (
            project_dir.parent != project_root
            or not caption_path.is_file()
            or caption_path.is_symlink()
        ):
            return ""
        return caption_path.read_text(encoding="utf-8").strip()

    @app.exception_handler(ReviewIncomplete)
    async def review_error(_request: Request, exc: ReviewIncomplete):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "REVIEW_INCOMPLETE", "message": str(exc), "recovery": "검수 네 항목을 모두 확인하세요."}},
        )

    @app.exception_handler(RightsNotApproved)
    async def rights_error(_request: Request, exc: RightsNotApproved):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "RIGHTS_NOT_APPROVED", "message": str(exc), "recovery": "자산의 사용 권한을 확인하세요."}},
        )

    @app.exception_handler(FinalVideoMissing)
    async def video_error(_request: Request, exc: FinalVideoMissing):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "FINAL_VIDEO_MISSING", "message": "최종 영상이 없습니다.", "recovery": "영상을 먼저 생성하세요."}},
        )

    @app.exception_handler(ValueError)
    async def input_error(_request: Request, exc: ValueError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_INPUT", "message": str(exc), "recovery": "입력값과 파일 형식을 확인하세요."}},
        )

    @app.exception_handler(GeminiError)
    async def gemini_error(_request: Request, exc: GeminiError):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "GEMINI_UNAVAILABLE",
                    "message": str(exc),
                    "recovery": "영상은 저장되어 있습니다. 잠시 후 영상 다시 분석을 눌러 주세요.",
                }
            },
        )

    @app.exception_handler(CostConfirmationRequired)
    async def cost_confirmation_error(_request: Request, exc: CostConfirmationRequired):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "COST_CONFIRMATION_REQUIRED", "message": str(exc)}},
        )

    @app.exception_handler(HiggsfieldError)
    async def higgsfield_error(_request: Request, exc: HiggsfieldError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "HIGGSFIELD_ERROR", "message": str(exc)}},
        )

    @app.exception_handler(MetaInstagramError)
    async def meta_instagram_error(_request: Request, exc: MetaInstagramError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "META_INSTAGRAM_ERROR", "message": str(exc)}},
        )

    @app.exception_handler(PexelsNotConfigured)
    async def pexels_config_error(_request: Request, exc: PexelsNotConfigured):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "PEXELS_NOT_CONFIGURED",
                    "message": str(exc),
                    "setup_url": "https://www.pexels.com/api/",
                }
            },
        )

    @app.exception_handler(ProjectNotFound)
    async def project_not_found(_request: Request, _exc: ProjectNotFound):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "PROJECT_NOT_FOUND", "message": "프로젝트를 찾을 수 없습니다."}},
        )

    @app.exception_handler(AssetNotFound)
    async def asset_not_found(_request: Request, _exc: AssetNotFound):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "ASSET_NOT_FOUND", "message": "자료를 찾을 수 없습니다."}},
        )

    @app.exception_handler(AutoBuildFailed)
    async def auto_build_error(_request: Request, exc: AutoBuildFailed):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "AUTO_BUILD_FAILED",
                    "stage": exc.stage,
                    "message": f"자동 생성 중 '{exc.stage}' 단계에서 멈췄습니다: {exc}",
                    "recovery": "입력값과 API 연결 상태를 확인한 뒤 다시 실행하세요.",
                }
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        ai_provider = "Gemini" if app_settings.gemini_api_key else "OpenAI"
        return templates.TemplateResponse(
            request,
            "index.html",
            {"offline": app_settings.offline, "ai_provider": ai_provider},
        )

    @app.get("/api/projects")
    async def list_projects(request: Request):
        return [project.model_dump(mode="json") for project in services(request).projects.list()]

    @app.get("/api/daily/products")
    async def list_daily_products(request: Request):
        return [
            item.model_dump(mode="json")
            for item in services(request).daily_queue.list()
        ]

    @app.post("/api/daily/products", status_code=status.HTTP_201_CREATED)
    async def add_daily_product(payload: DailyProductCreate, request: Request):
        item = services(request).daily_queue.add(payload)
        return item.model_dump(mode="json")

    @app.delete(
        "/api/daily/products/{product_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_daily_product(product_id: str, request: Request):
        try:
            services(request).daily_queue.delete(product_id)
        except LookupError as exc:
            raise HTTPException(404, "예약 상품을 찾을 수 없습니다.") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.put("/api/daily/products/{product_id}")
    async def update_daily_product(
        product_id: str, payload: DailyProductUpdate, request: Request
    ):
        try:
            item = services(request).daily_queue.update(product_id, payload)
        except LookupError as exc:
            raise HTTPException(404, "예약 상품을 찾을 수 없습니다.") from exc
        return item.model_dump(mode="json")

    @app.get("/api/daily/runs")
    async def list_daily_runs(request: Request):
        return [
            run.model_dump(mode="json")
            for run in services(request).daily_queue.list_runs()
        ]

    @app.get("/api/reels")
    async def list_reels(request: Request):
        service = services(request)
        result = []
        for item in service.reel_automation.list_recent():
            payload = item.model_dump(mode="json")
            payload["instagram_text"] = saved_instagram_text(
                service, item.project_id
            )
            result.append(payload)
        return result

    @app.put("/api/reels/{record_id}/published")
    async def mark_reel_published(record_id: str, request: Request):
        try:
            item = services(request).reel_automation.update(
                record_id, status="published", error_message=None
            )
        except LookupError as exc:
            raise HTTPException(404, "자동 생성 릴스를 찾을 수 없습니다.") from exc
        return item.model_dump(mode="json")

    @app.post("/api/reels/run-now", status_code=status.HTTP_201_CREATED)
    async def run_reels_now(request: Request):
        service = services(request)
        try:
            with daily_run_lock(service.settings.data_dir):
                runner = ReelDailyRunner(
                    service.reel_automation,
                    service.reel_planner,
                    lambda record, plan: service.reel_builder(service, record, plan),
                )
                result = await runner.run(datetime.now().astimezone().date(), count=2)
        except DailyRunInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post("/api/daily/run-now", status_code=status.HTTP_201_CREATED)
    async def run_daily_now(request: Request):
        service = services(request)
        try:
            with daily_run_lock(service.settings.data_dir):
                runner = DailyRunner(
                    service.daily_queue,
                    lambda product, index: service.daily_builder(
                        service, product, index
                    ),
                )
                run = await runner.run(limit=2)
        except DailyRunInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        return run.model_dump(mode="json")

    @app.get("/api/higgsfield/status")
    async def higgsfield_status(request: Request):
        return services(request).ai_assets.status()

    @app.get("/api/meta/status")
    async def meta_status(request: Request):
        return services(request).meta.status()

    @app.get("/api/video-profile")
    async def get_video_profile(request: Request):
        return services(request).video_profile.load().model_dump(mode="json")

    @app.put("/api/video-profile")
    async def save_video_profile(payload: VideoProfile, request: Request):
        saved = services(request).video_profile.save(payload)
        return saved.model_dump(mode="json")

    @app.get("/api/video-profile/reference-image/status")
    async def video_profile_reference_status(request: Request):
        exists = services(request).video_profile.has_reference()
        return {
            "exists": exists,
            "media_url": "/api/video-profile/reference-image/media" if exists else None,
        }

    @app.post(
        "/api/video-profile/reference-image",
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_video_profile_reference(
        request: Request, file: UploadFile = File(...)
    ):
        content = await file.read(20 * 1024 * 1024 + 1)
        service = services(request)
        service.video_profile.save_reference(
            file.filename or "reference-image",
            file.content_type or "application/octet-stream",
            content,
        )
        return {
            "exists": True,
            "media_url": "/api/video-profile/reference-image/media",
        }

    @app.get("/api/video-profile/reference-image/media")
    async def get_video_profile_reference(request: Request):
        path = services(request).video_profile.reference_path
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "브랜드 기준 이미지가 없습니다.")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/pexels/status")
    async def pexels_status(request: Request):
        return {
            "configured": services(request).pexels.configured,
            "setup_url": "https://www.pexels.com/api/",
        }

    @app.post("/api/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: ProjectCreate, request: Request):
        project = services(request).projects.create(payload)
        return project.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project_id: str, request: Request):
        service = services(request)
        project_lock = video_upload_locks.setdefault(project_id, asyncio.Lock())
        async with project_lock:
            project = service.projects.get(project_id)
            projects_dir = (service.settings.data_dir / "projects").resolve()
            project_dir = (projects_dir / project.id).resolve()
            if project_dir.parent != projects_dir:
                raise HTTPException(400, "프로젝트 경로가 올바르지 않습니다.")
            if project_dir.is_symlink():
                project_dir.unlink()
            elif project_dir.is_dir():
                shutil.rmtree(project_dir)
            export_path = service.settings.data_dir / "exports" / f"{project.id}.zip"
            if export_path.is_file() or export_path.is_symlink():
                export_path.unlink()
            service.projects.delete(project.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/projects/{project_id}/workspace")
    async def get_workspace(project_id: str, request: Request):
        service = services(request)
        project = service.projects.get(project_id)
        assets = service.assets.list_for_project(project_id)
        with service.db.connection() as conn:
            row = conn.execute(
                "SELECT content_json FROM scripts WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        draft = ScriptDraft.model_validate_json(row[0]) if row else None
        analysis = service.product_analysis.latest(project_id)
        attributions = {
            item["asset_id"]: item
            for item in service.pexels.list_attributions(project_id)
        }
        video = service.settings.data_dir / "projects" / project_id / "video-vertical.mp4"
        approved_assets = [asset for asset in assets if asset.rights_approved]
        max_step = 2
        if approved_assets and analysis is not None and analysis.confirmed:
            max_step = 3
        if draft is not None:
            max_step = 4
        if video.is_file() and not video.is_symlink():
            max_step = 5
        return {
            "project": project.model_dump(mode="json"),
            "assets": [
                {
                    "id": asset.id,
                    "filename": asset.filename,
                    "media_type": asset.media_type,
                    "content_type": asset.content_type,
                    "size_bytes": asset.size_bytes,
                    "rights_approved": asset.rights_approved,
                    "media_url": f"/api/projects/{project_id}/assets/{asset.id}/media",
                    "attribution": attributions.get(asset.id),
                }
                for asset in assets
            ],
            "references": [
                item.model_dump(mode="json")
                for item in service.references.list_for_project(project_id)
            ],
            "script": draft.model_dump(mode="json") if draft else None,
            "analysis": analysis.model_dump(mode="json") if analysis else None,
            "video_url": f"/api/projects/{project_id}/media/video" if max_step == 5 else None,
            "review": service.projects.get_review(project_id).model_dump(mode="json"),
            "max_step": max_step,
        }

    @app.post("/api/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
    async def upload_asset(project_id: str, request: Request, file: UploadFile = File(...)):
        content = await file.read(100 * 1024 * 1024 + 1)
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValueError("영상은 비어 있지 않은 100MB 이하 MP4 파일이어야 합니다.")
        asset = services(request).assets.save(
            project_id, file.filename or "upload", file.content_type or "application/octet-stream", content
        )
        return asset.model_dump(mode="json")

    @app.post(
        "/api/projects/{project_id}/videos/upload",
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_final_video(
        project_id: str, request: Request, file: UploadFile = File(...)
    ):
        service = services(request)
        filename = file.filename or "업로드영상.mp4"
        if Path(filename).suffix.lower() != ".mp4" or file.content_type != "video/mp4":
            raise ValueError("게시할 영상은 MP4 파일만 사용할 수 있습니다.")
        content = await file.read(100 * 1024 * 1024 + 1)
        upload_lock = video_upload_locks.setdefault(project_id, asyncio.Lock())
        async with upload_lock:
            service.projects.get(project_id)
            project_dir = service.settings.data_dir / "projects" / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            temporary = project_dir / f".video-upload-{uuid4().hex}.mp4"
            try:
                temporary.write_bytes(content)
                try:
                    metadata = await asyncio.to_thread(inspect_video, temporary)
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    raise ValueError(
                        "MP4 영상이 손상되었거나 실제 비디오 스트림이 없습니다."
                    ) from exc
                if metadata.width <= 0 or metadata.height <= 0 or metadata.duration_seconds <= 0:
                    raise ValueError(
                        "MP4 영상이 손상되었거나 실제 비디오 스트림이 없습니다."
                    )
                formats = {value.strip() for value in metadata.format_name.split(",")}
                if "mp4" not in formats:
                    raise ValueError("파일 이름만 MP4인 영상은 사용할 수 없습니다. 실제 MP4 형식으로 변환해 주세요.")
                asset = service.assets.save(project_id, filename, "video/mp4", content)
                asset = service.assets.approve_rights(asset.id, True)
                temporary.replace(project_dir / "video-vertical.mp4")
                service.instagram_publications.save(
                    project_id, InstagramPublication(project_id=project_id)
                )
                service.social_copy.repository.save(project_id, SocialCopy())
            finally:
                if temporary.is_file() or temporary.is_symlink():
                    temporary.unlink()
        return {
            "asset": asset.model_dump(mode="json", exclude={"path"}),
            "video_url": f"/api/projects/{project_id}/media/video",
        }

    @app.put("/api/projects/{project_id}/assets/{asset_id}/rights")
    async def update_rights(project_id: str, asset_id: str, payload: RightsUpdate, request: Request):
        asset = services(request).assets.get(asset_id)
        if asset.project_id != project_id:
            raise HTTPException(404, "자산을 찾을 수 없습니다.")
        updated = services(request).assets.approve_rights(asset_id, payload.approved)
        return updated.model_dump(mode="json")

    @app.delete(
        "/api/projects/{project_id}/assets/{asset_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_asset(project_id: str, asset_id: str, request: Request):
        service = services(request)
        asset = service.assets.get(asset_id)
        if asset.project_id != project_id:
            raise HTTPException(404, "자료를 찾을 수 없습니다.")
        service.assets.delete(asset.id)
        service.pexels.remove_attribution(project_id, asset.id)
        for stale_path in (
            service.settings.data_dir / "projects" / project_id / "video-vertical.mp4",
            service.settings.data_dir / "exports" / f"{project_id}.zip",
        ):
            if stale_path.is_file() or stale_path.is_symlink():
                stale_path.unlink()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/projects/{project_id}/assets/{asset_id}/media")
    async def get_asset_media(project_id: str, asset_id: str, request: Request):
        asset = services(request).assets.get(asset_id)
        if asset.project_id != project_id or not asset.path.is_file() or asset.path.is_symlink():
            raise HTTPException(404, "자료를 찾을 수 없습니다.")
        return FileResponse(asset.path, media_type=asset.content_type)

    @app.post(
        "/api/projects/{project_id}/analysis",
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_product_analysis(
        project_id: str, payload: AnalysisRequest, request: Request
    ):
        service = services(request)
        project = service.projects.get(project_id)
        selected = []
        for asset_id in payload.asset_ids:
            asset = service.assets.get(asset_id)
            if asset.project_id != project_id:
                raise HTTPException(404, "자료를 찾을 수 없습니다.")
            selected.append(asset)
        analysis = service.product_analysis.generate(project, selected)
        service.product_analysis.save(project_id, analysis)
        return analysis.model_dump(mode="json")

    @app.put("/api/projects/{project_id}/analysis")
    async def save_product_analysis(
        project_id: str, payload: ProductAnalysis, request: Request
    ):
        service = services(request)
        service.projects.get(project_id)
        service.product_analysis.save(project_id, payload)
        return payload.model_dump(mode="json")

    def resolve_start_image(service: Services, project_id: str, asset_id: str | None):
        if asset_id is None:
            return None
        asset = service.assets.get(asset_id)
        if asset.project_id != project_id or asset.media_type != "image" or not asset.rights_approved:
            raise HTTPException(422, "시작 이미지는 이 프로젝트의 사용 승인된 이미지여야 합니다.")
        return asset

    def resolve_ai_prompt(service: Services, payload: AIAssetRequest) -> str:
        if payload.kind == "video" and payload.use_video_profile:
            return service.video_profile.load().compose(payload.prompt)
        return payload.prompt.strip()

    def resolve_ai_start_image(
        service: Services, project_id: str, payload: AIAssetRequest
    ) -> tuple[Path | None, bool]:
        project_asset = resolve_start_image(
            service, project_id, payload.start_asset_id
        )
        if project_asset is not None:
            return project_asset.path, False
        if (
            payload.kind == "video"
            and payload.use_profile_reference
            and service.video_profile.has_reference()
        ):
            return service.video_profile.reference_path, True
        return None, False

    @app.post("/api/projects/{project_id}/ai-assets/cost")
    async def estimate_ai_asset(project_id: str, payload: AIAssetRequest, request: Request):
        service = services(request)
        service.projects.get(project_id)
        start_image, profile_reference_applied = resolve_ai_start_image(
            service, project_id, payload
        )
        effective_prompt = resolve_ai_prompt(service, payload)
        video_options = payload.video_options() if payload.kind == "video" else None
        result = service.ai_assets.estimate(
            payload.kind, effective_prompt, start_image, video_options
        )
        if payload.kind == "video":
            return {
                **result,
                "effective_prompt": effective_prompt,
                "profile_reference_applied": profile_reference_applied,
                "video_settings": video_options.as_dict(),
            }
        return result

    @app.post(
        "/api/projects/{project_id}/ai-assets",
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_ai_asset(project_id: str, payload: AIAssetRequest, request: Request):
        service = services(request)
        project = service.projects.get(project_id)
        start_image, profile_reference_applied = resolve_ai_start_image(
            service, project_id, payload
        )
        effective_prompt = resolve_ai_prompt(service, payload)
        video_options = payload.video_options() if payload.kind == "video" else None
        created = service.ai_assets.generate(
            project,
            payload.kind,
            effective_prompt,
            start_image,
            payload.confirmed_cost,
            video_options,
        )
        if payload.kind == "video":
            service.instagram_publications.save(
                project_id, InstagramPublication(project_id=project_id)
            )
        return {
            "assets": [
                asset.model_dump(mode="json", exclude={"path"}) for asset in created
            ],
            "effective_prompt": effective_prompt,
            "profile_reference_applied": profile_reference_applied,
            "video_settings": video_options.as_dict() if video_options else None,
        }

    @app.get("/api/projects/{project_id}/social-copy")
    async def get_social_copy(project_id: str, request: Request):
        service = services(request)
        service.projects.get(project_id)
        return service.social_copy.repository.load(project_id).model_dump(mode="json")

    @app.put("/api/projects/{project_id}/social-copy")
    async def save_social_copy(
        project_id: str, payload: SocialCopy, request: Request
    ):
        service = services(request)
        service.projects.get(project_id)
        return service.social_copy.repository.save(project_id, payload).model_dump(
            mode="json"
        )

    @app.post(
        "/api/projects/{project_id}/social-copy/generate",
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_social_copy(
        project_id: str, payload: SocialCopyGenerateRequest, request: Request
    ):
        service = services(request)
        upload_lock = video_upload_locks.setdefault(project_id, asyncio.Lock())
        async with upload_lock:
            service.projects.get(project_id)
            image_path = None
            image_content_type = None
            video_path = (
                service.settings.data_dir
                / "projects"
                / project_id
                / "video-vertical.mp4"
            )
            usable_video_path = (
                video_path
                if video_path.is_file() and not video_path.is_symlink()
                else None
            )
            if usable_video_path is None and payload.image_asset_id:
                asset = resolve_start_image(service, project_id, payload.image_asset_id)
                if asset is not None:
                    image_path = asset.path
                    image_content_type = asset.content_type
            elif usable_video_path is None and service.video_profile.has_reference():
                image_path = service.video_profile.reference_path
            value = await service.social_copy.generate(
                project_id,
                payload.prompt,
                image_path=image_path,
                image_content_type=image_content_type,
                video_path=usable_video_path,
                video_content_type="video/mp4",
            )
        return value.model_dump(mode="json")

    @app.post(
        "/api/projects/{project_id}/references",
        status_code=status.HTTP_201_CREATED,
    )
    async def add_reference(project_id: str, payload: ReferenceRequest, request: Request):
        service = services(request)
        service.projects.get(project_id)
        reference = service.references.save_youtube(project_id, payload.url)
        return reference.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/pexels/search")
    async def search_pexels(
        project_id: str,
        query: str,
        media_type: Literal["image", "video"],
        request: Request,
    ):
        service = services(request)
        service.projects.get(project_id)
        return {
            "items": [
                item.model_dump(mode="json")
                for item in service.pexels.search(query, media_type)
            ]
        }

    @app.post(
        "/api/projects/{project_id}/pexels/import",
        status_code=status.HTTP_201_CREATED,
    )
    async def import_pexels(
        project_id: str,
        payload: PexelsImportRequest,
        request: Request,
    ):
        service = services(request)
        service.projects.get(project_id)
        asset = service.pexels.import_item(
            project_id,
            payload.media_type,
            payload.pexels_id,
        )
        return {
            "asset": asset.model_dump(mode="json", exclude={"path"}),
            "attribution": service.pexels.list_attributions(project_id)[-1],
        }

    @app.post("/api/projects/{project_id}/script", status_code=status.HTTP_201_CREATED)
    async def generate_script(project_id: str, payload: ScriptOptions, request: Request):
        service = services(request)
        if service.script_provider is None:
            raise HTTPException(409, "OPENAI_API_KEY를 .env에 설정하세요.")
        project = service.projects.get(project_id)
        analysis = service.product_analysis.latest(project_id)
        if analysis is None or not analysis.confirmed:
            raise HTTPException(409, "확정한 상품 분석이 필요합니다.")
        draft = await service.script_provider.generate(
            ScriptRequest.from_project(
                project,
                payload.content_type,
                analysis=analysis,
                tone=payload.tone,
            )
        )
        return save_script_version(service, project_id, draft)

    @app.put("/api/projects/{project_id}/script")
    async def save_edited_script(project_id: str, payload: ScriptDraft, request: Request):
        return save_script_version(services(request), project_id, payload)

    @app.post("/api/projects/{project_id}/speech", status_code=status.HTTP_201_CREATED)
    async def generate_speech(project_id: str, request: Request):
        service = services(request)
        if service.speech_provider is None:
            raise HTTPException(409, "GEMINI_API_KEY 또는 OPENAI_API_KEY를 .env에 설정하세요.")
        draft = latest_script(service, project_id)
        text = " ".join(scene.narration for scene in draft.scenes)
        path = service.settings.data_dir / "projects" / project_id / "speech.wav"
        result = await service.speech_provider.generate(
            text, path, service.speech_voice, "밝고 자연스러운 한국어 쇼핑 영상처럼 말하세요."
        )
        return result.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/render", status_code=status.HTTP_201_CREATED)
    async def render_video(project_id: str, payload: RenderOptions, request: Request):
        service = services(request)
        project = service.projects.get(project_id)
        assets = service.assets.list_for_project(project_id)
        approved = service.assets.require_renderable([asset.id for asset in assets])
        draft = latest_script(service, project_id)
        project_dir = service.settings.data_dir / "projects" / project_id
        speech = project_dir / "speech.wav"
        output = project_dir / "video-vertical.mp4"
        result = service.renderer.render(
            RenderRequest(
                script=draft,
                asset_paths=[asset.path for asset in approved if asset.media_type in {"image", "video"}],
                asset_paths_by_id={
                    asset.id: asset.path
                    for asset in approved
                    if asset.media_type in {"image", "video"}
                },
                speech_path=speech,
                output_path=output,
                target_seconds=project.target_seconds,
            ),
            preview=payload.profile == "preview",
        )
        return result.model_dump(mode="json")

    @app.post(
        "/api/projects/{project_id}/auto-build",
        status_code=status.HTTP_201_CREATED,
    )
    async def auto_build(
        project_id: str, payload: AutoBuildOptions, request: Request
    ):
        return await run_auto_build(services(request), project_id, payload)

    def instagram_caption(service: Services, project_id: str) -> str:
        project = service.projects.get(project_id)
        saved = saved_instagram_text(service, project.id)
        if saved:
            return saved
        return (
            f"{AFFILIATE_DISCLOSURE}\n{AI_VOICE_DISCLOSURE}\n\n"
            f"{project.product_name}를 소개합니다. 자세한 내용은 프로필 링크에서 확인하세요.\n\n"
            "#과일 #제철과일 #강아지와사는집"
        )

    def update_instagram_status(
        service: Services, publication: InstagramPublication
    ) -> InstagramPublication:
        if not publication.container_id or publication.status == "published":
            return publication
        remote = service.meta.container_status(publication.container_id)
        remote_status = str(remote.get("status_code", "")).upper()
        if remote_status == "FINISHED":
            publication.status = "finished"
            publication.error = None
        elif remote_status in {"ERROR", "EXPIRED"}:
            publication.status = "error"
            publication.error = "Instagram이 영상을 처리하지 못했습니다. 다시 준비해 주세요."
        else:
            publication.status = "in_progress"
        return service.instagram_publications.save(
            publication.project_id, publication
        )

    @app.get("/api/public/videos/{token}")
    async def get_public_instagram_video(token: str, request: Request):
        service = services(request)
        if service.public_video_signer is None:
            raise HTTPException(404, "공개 영상 주소가 설정되지 않았습니다.")
        try:
            project_id = service.public_video_signer.verify(token, int(time.time()))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        service.projects.get(project_id)
        video = (
            service.settings.data_dir
            / "projects"
            / project_id
            / "video-vertical.mp4"
        )
        if not video.is_file() or video.is_symlink():
            raise HTTPException(404, "완성된 영상을 찾을 수 없습니다.")
        return FileResponse(video, media_type="video/mp4")

    @app.post(
        "/api/projects/{project_id}/instagram/prepare",
        status_code=status.HTTP_201_CREATED,
    )
    async def prepare_instagram_reel(project_id: str, request: Request):
        service = services(request)
        service.projects.get(project_id)
        video = (
            service.settings.data_dir
            / "projects"
            / project_id
            / "video-vertical.mp4"
        )
        if not video.is_file() or video.is_symlink():
            raise HTTPException(409, "완성된 영상이 없습니다.")
        if not service.meta.configured:
            raise HTTPException(409, "Instagram 연결 정보가 설정되지 않았습니다.")
        public_base = (service.settings.meta_public_base_url or "").strip().rstrip("/")
        if not public_base.startswith("https://"):
            raise HTTPException(
                409,
                "META_PUBLIC_BASE_URL에 외부에서 접근 가능한 HTTPS 주소를 설정해 주세요.",
            )
        if service.public_video_signer is None:
            raise HTTPException(409, "META_APP_SECRET을 설정해 주세요.")
        current = service.instagram_publications.load(project_id)
        if current.status in {"in_progress", "finished", "published"}:
            raise HTTPException(409, "이미 준비되었거나 게시된 프로젝트입니다.")
        token = service.public_video_signer.issue(
            project_id, int(time.time()) + 2 * 60 * 60
        )
        video_url = f"{public_base}/api/public/videos/{token}"
        container_id = service.meta.create_reel(
            video_url, instagram_caption(service, project_id)
        )
        publication = InstagramPublication(
            project_id=project_id,
            container_id=container_id,
            status="in_progress",
            prepared_at=publication_utc_now(),
        )
        return service.instagram_publications.save(
            project_id, publication
        ).model_dump(mode="json")

    @app.get("/api/projects/{project_id}/instagram/status")
    async def instagram_reel_status(project_id: str, request: Request):
        service = services(request)
        service.projects.get(project_id)
        publication = service.instagram_publications.load(project_id)
        if publication.container_id and publication.status not in {"published", "error"}:
            publication = update_instagram_status(service, publication)
        return publication.model_dump(mode="json")

    @app.post(
        "/api/projects/{project_id}/instagram/publish",
        status_code=status.HTTP_201_CREATED,
    )
    async def publish_instagram_reel(project_id: str, request: Request):
        service = services(request)
        service.projects.get(project_id)
        publication = service.instagram_publications.load(project_id)
        if publication.status == "published" or publication.media_id:
            raise HTTPException(409, "이미 Instagram에 게시된 영상입니다.")
        if not publication.container_id:
            raise HTTPException(409, "먼저 릴스 게시 준비를 실행해 주세요.")
        publication = update_instagram_status(service, publication)
        if publication.status != "finished":
            raise HTTPException(409, "Instagram 영상 처리가 아직 완료되지 않았습니다.")
        publication.media_id = service.meta.publish(publication.container_id)
        publication.status = "published"
        publication.published_at = publication_utc_now()
        publication.error = None
        return service.instagram_publications.save(
            project_id, publication
        ).model_dump(mode="json")

    @app.get("/api/projects/{project_id}/media/video")
    async def get_rendered_video(project_id: str, request: Request):
        service = services(request)
        service.projects.get(project_id)
        path = service.settings.data_dir / "projects" / project_id / "video-vertical.mp4"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "완성된 영상을 찾을 수 없습니다.")
        return FileResponse(path, media_type="video/mp4")

    @app.put("/api/projects/{project_id}/review")
    async def update_review(project_id: str, payload: ReviewState, request: Request):
        review = services(request).projects.update_review(project_id, payload)
        return review.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/export")
    async def export_project(project_id: str, request: Request):
        package = services(request).exports.build(project_id)
        return {"download_url": f"/api/exports/{package.zip_path.name}"}

    @app.get("/api/exports/{filename}")
    async def download_export(filename: str, request: Request):
        safe = Path(filename).name
        path = services(request).settings.data_dir / "exports" / safe
        if safe != filename or not path.is_file():
            raise HTTPException(404, "파일을 찾을 수 없습니다.")
        return FileResponse(path, media_type="application/zip", filename=filename)

    return app


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings.load(root)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
