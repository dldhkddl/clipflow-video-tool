from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


AFFILIATE_DISCLOSURE = (
    "이 게시물은 쿠팡파트너스 활동의 일환으로, "
    "이에 따른 일정액의 수수료를 제공받습니다."
)
AI_VOICE_DISCLOSURE = "이 영상의 내레이션은 AI 음성으로 제작되었습니다."


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    affiliate_url: HttpUrl
    product_name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    features: list[str] = Field(min_length=1, max_length=12)
    price_reference: str | None = Field(default=None, max_length=40)
    target_seconds: Literal[15, 30, 45, 60]
    platforms: set[Literal["youtube", "instagram"]] = Field(min_length=1)


class Project(ProjectCreate):
    id: str
    created_at: str
    updated_at: str


class Asset(BaseModel):
    id: str
    project_id: str
    filename: str
    media_type: Literal["image", "video", "audio"]
    content_type: str
    path: Path
    size_bytes: int
    rights_approved: bool


class AnalysisItem(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    origin: Literal["visual", "ai_promotion", "user"]


class ProductAnalysis(BaseModel):
    product_name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    visible_features: list[AnalysisItem] = Field(default_factory=list, max_length=12)
    package_text: list[AnalysisItem] = Field(default_factory=list, max_length=12)
    taste_notes: list[AnalysisItem] = Field(default_factory=list, max_length=8)
    use_cases: list[AnalysisItem] = Field(default_factory=list, max_length=8)
    promotion_points: list[AnalysisItem] = Field(default_factory=list, max_length=12)
    visual_direction: list[AnalysisItem] = Field(default_factory=list, max_length=8)
    source_asset_ids: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["demo", "vision"]
    confirmed: bool = False


class SceneDraft(BaseModel):
    order: int
    narration: str
    on_screen_text: str
    duration_seconds: float = Field(gt=0, le=15)
    asset_id: str | None = None
    asset_hint: str = ""


class ScriptDraft(BaseModel):
    hook: str
    selection_criteria: list[str]
    pros: list[str]
    cons: list[str]
    cta: str
    disclosure: str = AFFILIATE_DISCLOSURE
    scenes: list[SceneDraft] = Field(min_length=4, max_length=7)
    fact_check_notes: list[str] = Field(default_factory=list)


class ScriptRequest(BaseModel):
    product_name: str
    category: str
    features: list[str]
    price_reference: str | None = None
    target_seconds: Literal[15, 30, 45, 60]
    content_type: Literal["problem_solution", "comparison", "situational"]
    analysis: ProductAnalysis | None = None
    tone: Literal["friend", "shopping", "calm_review"] = "friend"

    @classmethod
    def from_project(
        cls,
        project: Project,
        content_type: str,
        analysis: ProductAnalysis | None = None,
        tone: str = "friend",
    ) -> "ScriptRequest":
        return cls(
            product_name=project.product_name,
            category=project.category,
            features=project.features,
            price_reference=project.price_reference,
            target_seconds=project.target_seconds,
            content_type=content_type,
            analysis=analysis,
            tone=tone,
        )


class Job(BaseModel):
    id: str
    project_id: str
    kind: str
    input_hash: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    output_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ReviewState(BaseModel):
    product_facts: bool = False
    asset_rights: bool = False
    honest_experience: bool = False
    affiliate_disclosure: bool = False
    reviewed_at: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(
            (
                self.product_facts,
                self.asset_rights,
                self.honest_experience,
                self.affiliate_disclosure,
            )
        )


class ExportPackage(BaseModel):
    zip_path: Path
    project_id: str
