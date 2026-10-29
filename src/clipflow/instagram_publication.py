import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel


PublicationStatus = Literal[
    "not_prepared", "in_progress", "finished", "error", "published"
]


class InstagramPublication(BaseModel):
    project_id: str
    container_id: str | None = None
    status: PublicationStatus = "not_prepared"
    media_id: str | None = None
    prepared_at: str | None = None
    published_at: str | None = None
    error: str | None = None


class InstagramPublicationRepository:
    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir.resolve()

    def _path(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise ValueError("프로젝트 ID가 올바르지 않습니다.")
        project_dir = (self.projects_dir / project_id).resolve()
        if project_dir.parent != self.projects_dir:
            raise ValueError("프로젝트 경로가 올바르지 않습니다.")
        return project_dir / "instagram-publication.json"

    def load(self, project_id: str) -> InstagramPublication:
        path = self._path(project_id)
        if not path.is_file() or path.is_symlink():
            return InstagramPublication(project_id=project_id)
        return InstagramPublication.model_validate_json(path.read_text(encoding="utf-8"))

    def save(
        self, project_id: str, value: InstagramPublication
    ) -> InstagramPublication:
        if value.project_id != project_id:
            raise ValueError("게시 기록의 프로젝트 ID가 일치하지 않습니다.")
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            value.model_dump_json(indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return value


class PublicVideoSigner:
    def __init__(self, secret: str, projects_dir: Path):
        if not secret:
            raise ValueError("서명 비밀값이 필요합니다.")
        self._secret = secret.encode("utf-8")
        self.projects_dir = projects_dir.resolve()

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def issue(self, project_id: str, expires_at: int) -> str:
        payload = json.dumps(
            {"project_id": project_id, "expires_at": int(expires_at)},
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = self._encode(payload)
        signature = hmac.new(
            self._secret, encoded.encode("ascii"), hashlib.sha256
        ).digest()
        return f"{encoded}.{self._encode(signature)}"

    def verify(self, token: str, now: int) -> str:
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(expected, self._decode(signature)):
                raise ValueError
            payload = json.loads(self._decode(encoded).decode("utf-8"))
            project_id = payload["project_id"]
            expires_at = int(payload["expires_at"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("영상 주소가 올바르지 않습니다.") from exc
        if expires_at < int(now):
            raise ValueError("영상 주소가 만료되었습니다.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise ValueError("영상 주소가 올바르지 않습니다.")
        return project_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
