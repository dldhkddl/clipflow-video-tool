from io import BytesIO
import json
from pathlib import Path, PurePath
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .db import Database
from .models import Asset, utc_now


class RightsNotApproved(ValueError):
    pass


class AssetNotFound(LookupError):
    pass


IMAGE_TYPES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
VIDEO_TYPES = {".mp4", ".mov"}
AUDIO_TYPES = {".wav", ".mp3"}
MAX_FILE_BYTES = 100 * 1024 * 1024


class AssetService:
    def __init__(self, db: Database, projects_dir: Path):
        self.db = db
        self.projects_dir = projects_dir

    def save(
        self,
        project_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> Asset:
        if PurePath(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("안전하지 않은 파일명입니다.")
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("파일 크기가 허용 범위를 벗어났습니다.")
        extension = Path(filename).suffix.lower()
        if extension in IMAGE_TYPES:
            media_type = "image"
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != IMAGE_TYPES[extension]:
                        raise ValueError("파일 확장자와 실제 형식이 다릅니다.")
                    image.verify()
            except UnidentifiedImageError as exc:
                raise ValueError("지원하지 않는 이미지 형식입니다.") from exc
        elif extension in VIDEO_TYPES:
            media_type = "video"
        elif extension in AUDIO_TYPES:
            media_type = "audio"
        else:
            raise ValueError("지원하지 않는 파일 형식입니다.")

        expected_prefix = {"image": "image/", "video": "video/", "audio": "audio/"}[media_type]
        if not content_type.startswith(expected_prefix):
            raise ValueError("파일 MIME 형식이 올바르지 않습니다.")

        asset_id = str(uuid4())
        relative = Path(project_id) / "assets" / f"{asset_id}{extension}"
        destination = self.projects_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        with self.db.connection() as conn:
            conn.execute(
                """INSERT INTO assets (
                    id, project_id, filename, media_type, content_type,
                    relative_path, size_bytes, rights_approved, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    asset_id,
                    project_id,
                    filename,
                    media_type,
                    content_type,
                    relative.as_posix(),
                    len(data),
                    utc_now(),
                ),
            )
        return self.get(asset_id)

    def get(self, asset_id: str) -> Asset:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise AssetNotFound(asset_id)
        return Asset(
            id=row["id"],
            project_id=row["project_id"],
            filename=row["filename"],
            media_type=row["media_type"],
            content_type=row["content_type"],
            path=self.projects_dir / Path(row["relative_path"]),
            size_bytes=row["size_bytes"],
            rights_approved=bool(row["rights_approved"]),
        )

    def approve_rights(self, asset_id: str, approved: bool) -> Asset:
        with self.db.connection() as conn:
            result = conn.execute(
                "UPDATE assets SET rights_approved = ? WHERE id = ?",
                (int(approved), asset_id),
            )
            if result.rowcount == 0:
                raise AssetNotFound(asset_id)
        return self.get(asset_id)

    def list_for_project(self, project_id: str) -> list[Asset]:
        with self.db.connection() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM assets WHERE project_id = ? ORDER BY created_at", (project_id,))]
        return [self.get(asset_id) for asset_id in ids]

    def delete(self, asset_id: str) -> Asset:
        asset = self.get(asset_id)
        projects_dir = self.projects_dir.resolve()
        asset_path = asset.path.resolve(strict=False)
        if not asset_path.is_relative_to(projects_dir):
            raise ValueError("자료 경로가 올바르지 않습니다.")
        if asset.path.is_symlink() or asset.path.is_file():
            asset.path.unlink()
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT id, content_json FROM scripts WHERE project_id = ?",
                (asset.project_id,),
            ).fetchall()
            for row in rows:
                content = json.loads(row["content_json"])
                changed = False
                for scene in content.get("scenes", []):
                    if scene.get("asset_id") == asset.id:
                        scene["asset_id"] = None
                        changed = True
                if changed:
                    conn.execute(
                        "UPDATE scripts SET content_json = ? WHERE id = ?",
                        (json.dumps(content, ensure_ascii=False), row["id"]),
                    )
            conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
        return asset

    def require_renderable(self, asset_ids: list[str]) -> list[Asset]:
        assets = [self.get(asset_id) for asset_id in asset_ids]
        if not assets or any(not asset.rights_approved for asset in assets):
            raise RightsNotApproved("모든 자산의 사용 권한을 확인해야 합니다.")
        return assets
