import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .db import Database
from .models import (
    AFFILIATE_DISCLOSURE,
    AI_VOICE_DISCLOSURE,
    ExportPackage,
)
from .projects import ProjectRepository


class ReviewIncomplete(RuntimeError):
    pass


class FinalVideoMissing(FileNotFoundError):
    pass


class ExportService:
    def __init__(
        self,
        db: Database,
        projects: ProjectRepository,
        projects_dir: Path,
        exports_dir: Path,
    ):
        self.db = db
        self.projects = projects
        self.projects_dir = projects_dir
        self.exports_dir = exports_dir

    def build(self, project_id: str) -> ExportPackage:
        return self._build(project_id, require_review=True, draft=False)

    def build_draft(self, project_id: str) -> ExportPackage:
        return self._build(project_id, require_review=False, draft=True)

    def _build(
        self, project_id: str, *, require_review: bool, draft: bool
    ) -> ExportPackage:
        project = self.projects.get(project_id)
        review = self.projects.get_review(project_id)
        if require_review and (not review.is_complete or not review.reviewed_at):
            raise ReviewIncomplete("네 가지 검수 항목을 모두 확인해야 합니다.")
        video = self.projects_dir / project_id / "video-vertical.mp4"
        if not video.is_file() or video.is_symlink():
            raise FinalVideoMissing(video)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        suffix = "-draft" if draft else ""
        zip_path = self.exports_dir / f"{project_id}{suffix}.zip"
        description = (
            f"{AFFILIATE_DISCLOSURE}\n{AI_VOICE_DISCLOSURE}\n\n"
            f"{project.product_name} 선택 기준을 정리했습니다.\n"
            f"상품 링크: {project.affiliate_url}\n"
        )
        instagram = (
            f"{AFFILIATE_DISCLOSURE}\n{AI_VOICE_DISCLOSURE}\n\n"
            f"{project.product_name}를 고를 때 확인할 기준을 정리했습니다. "
            "구매 링크는 프로필 링크에서 확인하세요.\n\n"
            "#상품추천 #선택가이드 #쿠팡파트너스"
        )
        summary = {
            "project_id": project.id,
            "product_name": project.product_name,
            "category": project.category,
            "features": project.features,
            "target_seconds": project.target_seconds,
            "platforms": sorted(project.platforms),
            "reviewed_at": review.reviewed_at,
            "review_status": (
                "reviewed" if review.is_complete and review.reviewed_at
                else "draft_needs_review"
            ),
        }
        files = {
            "youtube-title-options.txt": (
                f"{project.product_name}, 고르기 전에 볼 3가지\n"
                f"{project.product_name} 선택 기준 30초 정리\n"
                f"이런 분께 {project.product_name}이 맞습니다"
            ).encode("utf-8"),
            "youtube-description.txt": description.encode("utf-8"),
            "youtube-pinned-comment.txt": (
                f"{AFFILIATE_DISCLOSURE}\n상품 링크: {project.affiliate_url}"
            ).encode("utf-8"),
            "instagram-caption.txt": instagram.encode("utf-8"),
            "instagram-hashtags.txt": "#상품추천 #선택가이드 #쿠팡파트너스".encode("utf-8"),
            "disclosure.txt": f"{AFFILIATE_DISCLOSURE}\n{AI_VOICE_DISCLOSURE}".encode("utf-8"),
            "project-summary.json": json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"),
        }
        attribution_path = self.projects_dir / project_id / "pexels-attributions.json"
        if attribution_path.is_file() and not attribution_path.is_symlink():
            attributions = json.loads(attribution_path.read_text(encoding="utf-8"))
            credit_lines = ["무료 사진·영상 출처: Pexels", "https://www.pexels.com/", ""]
            for item in attributions:
                credit_lines.extend(
                    [
                        f"작가: {item['creator']}",
                        f"작가 페이지: {item['creator_url']}",
                        f"자료 페이지: {item['media_url']}",
                        "",
                    ]
                )
            files["pexels-credits.txt"] = "\n".join(credit_lines).encode("utf-8")
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(video, "video-vertical.mp4")
            for name, payload in files.items():
                archive.writestr(name, payload)
        return ExportPackage(zip_path=zip_path, project_id=project_id)
