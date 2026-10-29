from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import uvicorn

from .config import Settings
from .instagram_publication import PublicVideoSigner


def create_public_media_app(settings: Settings | None = None) -> FastAPI:
    root = Path(__file__).resolve().parents[2]
    app_settings = settings or Settings.load(root)
    secret = (
        app_settings.meta_app_secret.get_secret_value()
        if app_settings.meta_app_secret is not None
        else ""
    )
    signer = PublicVideoSigner(secret, app_settings.data_dir / "projects")
    app = FastAPI(
        title="ClipFlow signed media gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/public/videos/{token}", include_in_schema=False)
    async def get_signed_video(token: str):
        try:
            project_id = signer.verify(token, int(time.time()))
        except ValueError as exc:
            raise HTTPException(404, "영상 주소가 올바르지 않거나 만료되었습니다.") from exc
        projects_dir = (app_settings.data_dir / "projects").resolve()
        video = (projects_dir / project_id / "video-vertical.mp4").resolve()
        if video.parent.parent != projects_dir:
            raise HTTPException(404, "영상을 찾을 수 없습니다.")
        if not video.is_file() or video.is_symlink():
            raise HTTPException(404, "영상을 찾을 수 없습니다.")
        return FileResponse(
            video,
            media_type="video/mp4",
            headers={"Cache-Control": "private, no-store"},
        )

    return app


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    uvicorn.run(
        create_public_media_app(Settings.load(root)),
        host="127.0.0.1",
        port=8767,
    )


if __name__ == "__main__":
    main()
