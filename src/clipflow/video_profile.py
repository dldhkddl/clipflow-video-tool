from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator


MAX_REFERENCE_BYTES = 20 * 1024 * 1024


class VideoProfile(BaseModel):
    enabled: bool = True
    base_prompt: str = Field(
        default=(
            "실제 반려견과 사람용 과일 상품을 촬영한 자연스러운 세로 광고 영상. "
            "강아지의 외형과 털무늬, 상품 포장과 배경 구조를 유지한다. "
            "강아지와 과일 상자, 주방 배경 전체가 선명하다. "
            "자연광, 사실적인 가정집 실내, deep depth of field, "
            "background in sharp focus"
        ),
        min_length=3,
        max_length=1000,
    )
    negative_prompt: str = Field(
        default=(
            "bokeh, blurred background, shallow depth of field, distorted dog, "
            "altered fur pattern, extra limbs, deformed fruit, warped package text"
        ),
        max_length=600,
    )
    lens_mm: int = Field(default=35, ge=12, le=135)
    aperture: str = Field(default="f/8", min_length=2, max_length=12)
    camera_motion: str = Field(default="고정 또는 매우 느린 이동", min_length=2, max_length=120)

    @field_validator("base_prompt", "negative_prompt", "aperture", "camera_motion")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    def compose(self, scene_prompt: str) -> str:
        scene = scene_prompt.strip()
        if not self.enabled:
            return scene
        parts = [
            f"브랜드 공통 연출: {self.base_prompt}",
            f"카메라 기본값: {self.lens_mm}mm, {self.aperture}, {self.camera_motion}",
        ]
        if self.negative_prompt:
            parts.append(f"피해야 할 표현: {self.negative_prompt}")
        parts.append(f"오늘의 장면: {scene}")
        return "\n".join(parts)


class VideoProfileRepository:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> VideoProfile:
        if not self.path.is_file() or self.path.is_symlink():
            return VideoProfile()
        return VideoProfile.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, profile: VideoProfile) -> VideoProfile:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return profile

    @property
    def reference_path(self) -> Path:
        return self.path.parent / "video-profile-reference.png"

    def has_reference(self) -> bool:
        return self.reference_path.is_file() and not self.reference_path.is_symlink()

    def save_reference(self, filename: str, content_type: str, data: bytes) -> Path:
        if not data or len(data) > MAX_REFERENCE_BYTES:
            raise ValueError("기준 이미지는 20MB 이하만 업로드할 수 있습니다.")
        if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("기준 이미지는 JPG, PNG, WEBP 형식만 지원합니다.")
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("기준 이미지의 파일 형식이 올바르지 않습니다.")
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                self.reference_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.reference_path.with_suffix(".tmp")
                converted.save(temporary, format="PNG")
                temporary.replace(self.reference_path)
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("기준 이미지 파일을 읽을 수 없습니다.") from exc
        return self.reference_path
