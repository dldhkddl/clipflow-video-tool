import logging
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .assets import RightsNotApproved
from .media.render import RenderRequest
from .models import ReviewState, ScriptDraft, ScriptRequest, utc_now
from .pexels import PexelsNotConfigured


logger = logging.getLogger(__name__)


def used_pexels_ids(projects_dir: Path) -> set[int]:
    used: set[int] = set()
    if not projects_dir.is_dir() or projects_dir.is_symlink():
        return used
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.is_symlink():
            continue
        path = project_dir / "pexels-attributions.json"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("pexels_id") is not None:
                    used.add(int(item["pexels_id"]))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("Ignoring invalid Pexels attribution file: %s", path)
    return used


class AutoBuildOptions(BaseModel):
    query: str | None = Field(default=None, max_length=120)
    media_type: Literal["image", "video"] = "video"
    asset_count: int = Field(default=3, ge=1, le=5)
    tone: Literal["friend", "shopping", "calm_review"] = "friend"
    content_type: Literal["problem_solution", "comparison", "situational"] = (
        "problem_solution"
    )
    pexels_page: int = Field(default=1, ge=1, le=80)


class AutoBuildFailed(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(message)


def save_script_version(service: Any, project_id: str, draft: ScriptDraft) -> dict:
    project = service.projects.get(project_id)
    total_seconds = sum(scene.duration_seconds for scene in draft.scenes)
    if abs(total_seconds - project.target_seconds) > 0.05:
        raise ValueError(
            f"장면 길이 합계를 {project.target_seconds}초로 맞춰 주세요. "
            f"현재 {total_seconds:g}초입니다."
        )
    draft = draft.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"order": index})
                for index, scene in enumerate(draft.scenes, start=1)
            ]
        }
    )
    script_id = str(uuid4())
    with service.db.connection() as conn:
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM scripts WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO scripts (id, project_id, version, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (script_id, project_id, version, draft.model_dump_json(), utc_now()),
        )
    path = service.settings.data_dir / "projects" / project_id / "script.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    return {"id": script_id, "draft": draft.model_dump(mode="json")}


def latest_script(service: Any, project_id: str) -> ScriptDraft:
    with service.db.connection() as conn:
        row = conn.execute(
            "SELECT content_json FROM scripts WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(409, "대본을 먼저 생성하세요.")
    return ScriptDraft.model_validate_json(row[0])


async def run_auto_build(
    service: Any, project_id: str, payload: AutoBuildOptions
) -> dict:
    project = service.projects.get(project_id)
    completed: list[str] = []
    stage = "assets"
    try:
        service.projects.update_review(project_id, ReviewState())
        assets = [
            asset
            for asset in service.assets.list_for_project(project_id)
            if asset.rights_approved and asset.media_type in {"image", "video"}
        ]
        needed = payload.asset_count - len(assets)
        if needed > 0:
            query = (payload.query or "").strip() or (
                f"{project.product_name} {project.category}"
            )
            imported_pexels_ids = used_pexels_ids(
                service.settings.data_dir / "projects"
            )
            candidates = []
            candidate_ids: set[int] = set()
            for offset in range(10):
                page = ((payload.pexels_page - 1 + offset) % 80) + 1
                search_results = await run_in_threadpool(
                    service.pexels.search,
                    query,
                    payload.media_type,
                    page,
                )
                for item in search_results:
                    if (
                        item.id not in imported_pexels_ids
                        and item.id not in candidate_ids
                    ):
                        candidates.append(item)
                        candidate_ids.add(item.id)
                if len(candidates) >= needed:
                    break
            if len(candidates) < needed:
                raise ValueError(
                    f"Pexels 검색 결과가 부족합니다. 필요한 자료 {needed}개 중 "
                    f"{len(candidates)}개를 찾았습니다."
                )
            for item in candidates[:needed]:
                assets.append(
                    await run_in_threadpool(
                        service.pexels.import_item,
                        project_id,
                        payload.media_type,
                        item.id,
                    )
                )
        selected_assets = assets[: payload.asset_count]
        completed.append(stage)

        stage = "analysis"
        analysis = await run_in_threadpool(
            service.product_analysis.generate, project, selected_assets
        )
        analysis = analysis.model_copy(update={"confirmed": True})
        service.product_analysis.save(project_id, analysis)
        completed.append(stage)

        stage = "script"
        if service.script_provider is None:
            raise ValueError(
                "GEMINI_API_KEY 또는 OPENAI_API_KEY를 .env에 설정하세요."
            )
        draft = await service.script_provider.generate(
            ScriptRequest.from_project(
                project,
                payload.content_type,
                analysis=analysis,
                tone=payload.tone,
            )
        )
        save_script_version(service, project_id, draft)
        completed.append(stage)

        stage = "speech"
        if service.speech_provider is None:
            raise ValueError(
                "GEMINI_API_KEY 또는 OPENAI_API_KEY를 .env에 설정하세요."
            )
        narration = " ".join(scene.narration for scene in draft.scenes)
        project_dir = service.settings.data_dir / "projects" / project_id
        speech_path = project_dir / "speech.wav"
        await service.speech_provider.generate(
            narration,
            speech_path,
            service.speech_voice,
            "밝고 자연스러운 한국어 쇼핑 영상처럼 말하세요.",
        )
        completed.append(stage)

        stage = "video"
        video_path = project_dir / "video-vertical.mp4"
        await run_in_threadpool(
            service.renderer.render,
            RenderRequest(
                script=draft,
                asset_paths=[asset.path for asset in selected_assets],
                asset_paths_by_id={asset.id: asset.path for asset in selected_assets},
                speech_path=speech_path,
                output_path=video_path,
                target_seconds=project.target_seconds,
            ),
            preview=False,
        )
        completed.append(stage)

        stage = "draft_zip"
        package = await run_in_threadpool(service.exports.build_draft, project_id)
        completed.append(stage)
    except Exception as exc:
        if isinstance(exc, AutoBuildFailed):
            raise
        if isinstance(exc, (ValueError, PexelsNotConfigured, RightsNotApproved)):
            public_message = str(exc)
        else:
            logger.exception("Automatic build failed at stage %s", stage)
            public_message = "연결된 서비스 또는 영상 처리 중 오류가 발생했습니다."
        raise AutoBuildFailed(stage, public_message) from exc

    return {
        "status": "completed",
        "project_id": project_id,
        "asset_count": len(selected_assets),
        "completed_steps": completed,
        "review_required": True,
        "video_url": f"/api/projects/{project_id}/media/video",
        "draft_download_url": f"/api/exports/{package.zip_path.name}",
    }
