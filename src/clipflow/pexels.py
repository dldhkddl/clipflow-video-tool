import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, HttpUrl

from .assets import MAX_FILE_BYTES, AssetService
from .models import Asset


class PexelsNotConfigured(RuntimeError):
    pass


class PexelsItem(BaseModel):
    id: int
    media_type: Literal["image", "video"]
    preview_url: HttpUrl
    media_url: HttpUrl
    pexels_url: HttpUrl
    creator: str
    creator_url: HttpUrl
    width: int
    height: int
    duration_seconds: int | None = None


JsonGetter = Callable[[str, dict[str, object]], dict]
BytesGetter = Callable[[str, int], tuple[bytes, str]]


class PexelsService:
    API_ROOT = "https://api.pexels.com"

    def __init__(
        self,
        api_key: str | None,
        assets: AssetService,
        projects_dir: Path,
        *,
        json_get: JsonGetter | None = None,
        bytes_get: BytesGetter | None = None,
    ):
        self.api_key = api_key.strip() if api_key else None
        self.assets = assets
        self.projects_dir = projects_dir
        self.json_get = json_get or self._default_json_get
        self.bytes_get = bytes_get or self._default_bytes_get

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        media_type: Literal["image", "video"],
        page: int = 1,
    ) -> list[PexelsItem]:
        self._require_key()
        cleaned = query.strip()
        if len(cleaned) < 2:
            raise ValueError("Pexels 검색어를 두 글자 이상 입력하세요.")
        path = "/v1/search" if media_type == "image" else "/v1/videos/search"
        payload = self.json_get(
            path,
            {
                "query": cleaned,
                "orientation": "portrait",
                "locale": "ko-KR",
                "per_page": 8,
                "page": max(1, min(page, 80)),
            },
        )
        raw_items = payload.get("photos", []) if media_type == "image" else payload.get("videos", [])
        return [self._photo_item(item) if media_type == "image" else self._video_item(item) for item in raw_items]

    def import_item(
        self,
        project_id: str,
        media_type: Literal["image", "video"],
        pexels_id: int,
    ) -> Asset:
        self._require_key()
        path = f"/v1/photos/{pexels_id}" if media_type == "image" else f"/v1/videos/videos/{pexels_id}"
        detail = self.json_get(path, {})
        item = self._photo_item(detail) if media_type == "image" else self._video_item(detail)
        download_url = str(item.media_url)
        host = (urlparse(download_url).hostname or "").lower()
        if host != "pexels.com" and not host.endswith(".pexels.com"):
            raise ValueError("Pexels에서 제공한 자료 주소만 가져올 수 있습니다.")
        content, content_type = self.bytes_get(download_url, MAX_FILE_BYTES)
        extension = ".jpg" if media_type == "image" else ".mp4"
        expected_type = "image/jpeg" if media_type == "image" else "video/mp4"
        if not content_type.startswith(expected_type):
            raise ValueError("Pexels 자료 형식이 올바르지 않습니다.")
        asset = self.assets.save(
            project_id,
            f"pexels-{media_type}-{pexels_id}{extension}",
            expected_type,
            content,
        )
        asset = self.assets.approve_rights(asset.id, True)
        self._save_attribution(project_id, asset.id, item)
        return asset

    def list_attributions(self, project_id: str) -> list[dict]:
        path = self._attribution_path(project_id)
        if not path.is_file() or path.is_symlink():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def remove_attribution(self, project_id: str, asset_id: str) -> None:
        path = self._attribution_path(project_id)
        if not path.is_file() or path.is_symlink():
            return
        credits = [item for item in self.list_attributions(project_id) if item.get("asset_id") != asset_id]
        path.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")

    def _require_key(self) -> None:
        if not self.configured:
            raise PexelsNotConfigured(
                "무료 Pexels API 키가 필요합니다. 발급 후 .env의 PEXELS_API_KEY에 넣어 주세요."
            )

    def _default_json_get(self, path: str, params: dict[str, object]) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.API_ROOT}{path}{query}",
            headers={"Authorization": self.api_key or "", "User-Agent": "ClipFlow-AI/0.1"},
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _default_bytes_get(url: str, limit: int) -> tuple[bytes, str]:
        request = Request(url, headers={"User-Agent": "ClipFlow-AI/0.1"})
        with urlopen(request, timeout=60) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > limit:
                raise ValueError("Pexels 자료가 100MB 제한을 넘습니다.")
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Pexels 자료가 100MB 제한을 넘습니다.")
            content_type = response.headers.get_content_type()
            return content, content_type

    @staticmethod
    def _photo_item(item: dict) -> PexelsItem:
        source = item.get("src") or {}
        return PexelsItem(
            id=item["id"],
            media_type="image",
            preview_url=source.get("medium") or source.get("portrait"),
            media_url=source.get("portrait") or source.get("large2x") or source.get("original"),
            pexels_url=item["url"],
            creator=item["photographer"],
            creator_url=item["photographer_url"],
            width=item["width"],
            height=item["height"],
        )

    @classmethod
    def _video_item(cls, item: dict) -> PexelsItem:
        video_file = cls._preferred_video_file(item.get("video_files") or [])
        user = item.get("user") or {}
        return PexelsItem(
            id=item["id"],
            media_type="video",
            preview_url=item["image"],
            media_url=video_file["link"],
            pexels_url=item["url"],
            creator=user.get("name") or "Pexels Creator",
            creator_url=user.get("url") or "https://www.pexels.com/",
            width=video_file.get("width") or item["width"],
            height=video_file.get("height") or item["height"],
            duration_seconds=item.get("duration"),
        )

    @staticmethod
    def _preferred_video_file(files: list[dict]) -> dict:
        candidates = [item for item in files if item.get("file_type") == "video/mp4" and item.get("link")]
        if not candidates:
            raise ValueError("가져올 수 있는 Pexels MP4 영상이 없습니다.")

        def score(item: dict) -> tuple[int, int, int, int]:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            return (
                int(height >= width),
                int(item.get("quality") == "hd"),
                int(width <= 1080 and height <= 1920),
                min(width * height, 1080 * 1920),
            )

        return max(candidates, key=score)

    def _attribution_path(self, project_id: str) -> Path:
        return self.projects_dir / project_id / "pexels-attributions.json"

    def _save_attribution(self, project_id: str, asset_id: str, item: PexelsItem) -> None:
        credits = self.list_attributions(project_id)
        credits.append(
            {
                "asset_id": asset_id,
                "creator": item.creator,
                "creator_url": str(item.creator_url),
                "media_url": str(item.pexels_url),
                "pexels_id": item.id,
                "type": item.media_type,
            }
        )
        path = self._attribution_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
