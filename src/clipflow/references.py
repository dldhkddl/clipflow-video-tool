import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class VideoReference(BaseModel):
    id: str
    source: str = "youtube"
    original_url: str
    embed_url: str
    renderable: bool = False


class ReferenceService:
    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir

    def save_youtube(self, project_id: str, url: str) -> VideoReference:
        video_id = self._youtube_id(url)
        reference = VideoReference(
            id=video_id,
            original_url=url,
            embed_url=f"https://www.youtube-nocookie.com/embed/{video_id}",
        )
        references = self.list_for_project(project_id)
        if all(item.id != video_id for item in references):
            references.append(reference)
            self._write(project_id, references)
        return reference

    def list_for_project(self, project_id: str) -> list[VideoReference]:
        path = self._path(project_id)
        if not path.is_file() or path.is_symlink():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [VideoReference.model_validate(item) for item in payload]

    def _write(self, project_id: str, references: list[VideoReference]) -> None:
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in references],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _path(self, project_id: str) -> Path:
        return self.projects_dir / project_id / "references.json"

    @staticmethod
    def _youtube_id(url: str) -> str:
        parsed = urlparse(url.strip())
        host = parsed.hostname.lower() if parsed.hostname else ""
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/").split("/")[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 2 and parts[0] in {"shorts", "embed"}:
                video_id = parts[1]
            elif parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
        if parsed.scheme not in {"http", "https"} or not VIDEO_ID.fullmatch(video_id):
            raise ValueError("올바른 YouTube 영상 또는 Shorts 링크를 입력하세요.")
        return video_id
