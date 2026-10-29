import asyncio
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field, ValidationError

from .providers.gemini import GeminiClient, GeminiError


SOCIAL_COPY_PROMPT = """
Instagram 릴스에 올릴 한국어 설명과 해시태그를 작성한다.
첨부 영상이 있으면 영상 전체의 화면, 등장 캐릭터, 장소, 행동을 직접 분석한다.
영상의 음성, 한국어 대사, 효과음과 배경음도 함께 듣고 실제 내용에 반영한다.
첨부 영상과 사용자 프롬프트가 다르면 실제 영상에서 확인되는 내용을 우선한다.
영상이 없을 때만 영상 프롬프트와 첨부 이미지를 근거로 쓴다.
첫 문장은 사건의 다음 결과가 궁금해지는 자연스러운 문장으로 시작한다.
예: 오늘 면접 본 줄라이, 과연 어떻게 될까요?
딱딱한 보고서 말투와 과장된 효능, 확인되지 않은 사실은 쓰지 않는다.
설명은 필요한 만큼 2문장 이상 자연스럽게 작성한다.
해시태그는 한국어 중심으로 5~12개 작성하며 #을 포함한다.
""".strip()


class SocialCopy(BaseModel):
    description: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=12)

    def normalized(self) -> "SocialCopy":
        tags: list[str] = []
        for raw in self.hashtags:
            value = re.sub(r"[^0-9A-Za-z가-힣_]", "", raw.lstrip("#").replace(" ", ""))
            if value and f"#{value}" not in tags:
                tags.append(f"#{value}")
        return SocialCopy(description=self.description.strip(), hashtags=tags[:12])

    def instagram_text(self) -> str:
        value = self.normalized()
        parts = [value.description]
        if value.hashtags:
            parts.append(" ".join(value.hashtags))
        return "\n\n".join(part for part in parts if part).strip()


class SocialCopyRepository:
    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir.resolve()

    def _project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise ValueError("프로젝트 ID가 올바르지 않습니다.")
        directory = (self.projects_dir / project_id).resolve()
        if directory.parent != self.projects_dir:
            raise ValueError("프로젝트 경로가 올바르지 않습니다.")
        return directory

    def load(self, project_id: str) -> SocialCopy:
        path = self._project_dir(project_id) / "게시문구.json"
        if not path.is_file() or path.is_symlink():
            return SocialCopy()
        return SocialCopy.model_validate_json(path.read_text(encoding="utf-8")).normalized()

    def save(self, project_id: str, value: SocialCopy) -> SocialCopy:
        value = value.normalized()
        directory = self._project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "게시문구.json"
        temporary = json_path.with_suffix(".json.tmp")
        temporary.write_text(value.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(json_path)
        (directory / "인스타그램-문구.txt").write_text(
            value.instagram_text(), encoding="utf-8"
        )
        return value


class SocialCopyService:
    def __init__(
        self,
        repository: SocialCopyRepository,
        *,
        offline: bool,
        gemini_api_key: str | None,
        gemini_model: str,
    ):
        self.repository = repository
        self.offline = offline
        self.client = (
            GeminiClient(gemini_api_key, gemini_model)
            if gemini_api_key and not offline
            else None
        )

    async def generate(
        self,
        project_id: str,
        prompt: str,
        image_path: Path | None = None,
        image_content_type: str | None = None,
        video_path: Path | None = None,
        video_content_type: str | None = None,
    ) -> SocialCopy:
        if self.client is None:
            value = self._offline_copy(prompt)
        elif video_path is not None:
            with TemporaryDirectory(prefix="clipflow-social-") as directory:
                snapshot = Path(directory) / "분석영상.mp4"
                shutil.copyfile(video_path, snapshot)
                video_digest = self._file_digest(snapshot)
                if self._file_digest(video_path) != video_digest:
                    raise GeminiError("분석 중 영상이 변경되었습니다. 새 영상을 다시 분석해 주세요.")
                payload = await asyncio.to_thread(
                    self.client.generate_json_from_file,
                    prompt=(prompt.strip() or "첨부 영상의 실제 화면과 음성·대사를 분석해 Instagram 게시 문구를 작성하세요."),
                    schema=SocialCopy.model_json_schema(),
                    system_prompt=SOCIAL_COPY_PROMPT,
                    file_path=snapshot,
                    content_type=video_content_type or "video/mp4",
                )
                value = self._validate_payload(payload)
                if not video_path.is_file() or self._file_digest(video_path) != video_digest:
                    raise GeminiError("분석 중 영상이 변경되었습니다. 새 영상을 다시 분석해 주세요.")
        else:
            image_parts = None
            if image_path is not None:
                content_type = image_content_type or mimetypes.guess_type(image_path.name)[0]
                if content_type and content_type.startswith("image/"):
                    image_parts = [{
                        "inline_data": {
                            "mime_type": content_type,
                            "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                        }
                    }]
            payload = await asyncio.to_thread(
                self.client.generate_json,
                prompt=prompt.strip(),
                schema=SocialCopy.model_json_schema(),
                system_prompt=SOCIAL_COPY_PROMPT,
                image_parts=image_parts,
            )
            value = self._validate_payload(payload)
        return self.repository.save(project_id, value)

    @staticmethod
    def _validate_payload(payload: dict) -> SocialCopy:
        try:
            return SocialCopy.model_validate(payload)
        except ValidationError as exc:
            raise GeminiError("Gemini 게시 문구 응답 형식이 올바르지 않습니다. 다시 분석해 주세요.") from exc

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _offline_copy(prompt: str) -> SocialCopy:
        clean = " ".join(prompt.split()) or "업로드한 영상의 이야기"
        dog = "줄라이" if "줄라이" in clean else "강아지"
        if "면접" in clean:
            description = (
                f"오늘 면접 본 {dog}, 과연 어떻게 될까요? "
                "긴장했지만 끝까지 최선을 다하는 모습을 영상에서 확인해 보세요."
            )
            scene_tags = ["#면접", "#직장인강아지"]
        else:
            description = (
                f"오늘 {dog}에게 무슨 일이 생긴 걸까요? "
                f"{clean[:120]} 결과는 영상에서 확인해 보세요."
            )
            scene_tags = ["#강아지영상", "#오늘의이야기"]
        return SocialCopy(
            description=description,
            hashtags=["#줄라이", "#시바견", "#강아지릴스", *scene_tags, "#샘플업체"],
        ).normalized()
