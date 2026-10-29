from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from .assets import AssetService, MAX_FILE_BYTES
from .models import Asset, Project
from .providers.higgsfield import HiggsfieldCLI, VideoGenerationOptions


class CostConfirmationRequired(RuntimeError):
    pass


class AIAssetService:
    def __init__(self, assets: AssetService, offline: bool):
        self.assets = assets
        self.offline = offline
        self.higgsfield = HiggsfieldCLI()
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    def status(self) -> dict:
        if self.offline:
            return {
                "available": True,
                "authenticated": False,
                "offline_demo": True,
                "message": "데모 모드: 실제 과금 없이 AI 자료 생성을 체험합니다.",
            }
        return {**self.higgsfield.status(), "offline_demo": False}

    def estimate(
        self,
        kind: str,
        prompt: str,
        start_image: Path | None,
        video_options: VideoGenerationOptions | None = None,
    ) -> dict:
        if self.offline:
            return {"credits": 0, "offline_demo": True, "requires_confirmation": True}
        result = (
            self.higgsfield.estimate_image(prompt)
            if kind == "image"
            else self.higgsfield.estimate_video(prompt, start_image, video_options)
        )
        return {
            "credits": result["credits"],
            "offline_demo": False,
            "requires_confirmation": True,
        }

    def generate(
        self,
        project: Project,
        kind: str,
        prompt: str,
        start_image: Path | None,
        confirmed_cost: bool,
        video_options: VideoGenerationOptions | None = None,
    ) -> list[Asset]:
        if not confirmed_cost:
            raise CostConfirmationRequired("생성 비용을 확인하고 동의해야 합니다.")
        created: list[Asset] = []
        if kind == "image":
            created.append(self._create_image(project, prompt))
            return created
        if start_image is None and self.offline:
            generated_start = self._create_image(project, prompt)
            created.append(generated_start)
            start_image = generated_start.path
        created.append(self._create_video(project, prompt, start_image, video_options))
        return created

    def _create_image(self, project: Project, prompt: str) -> Asset:
        if self.offline:
            data = self._demo_image(project.product_name, prompt)
        else:
            result = self.higgsfield.generate_image(prompt)
            data = self._download(result["url"], "image/")
        asset = self.assets.save(
            project.id, f"ai-image-{uuid4().hex[:8]}.png", "image/png", data
        )
        return self.assets.approve_rights(asset.id, True)

    def _create_video(
        self,
        project: Project,
        prompt: str,
        start_image: Path | None,
        video_options: VideoGenerationOptions | None,
    ) -> Asset:
        if self.offline:
            if start_image is None:
                raise ValueError("데모 영상에는 시작 이미지가 필요합니다.")
            data = self._demo_video(start_image)
        else:
            result = self.higgsfield.generate_video(prompt, start_image, video_options)
            data = self._download(result["url"], "video/")
        asset = self.assets.save(
            project.id, f"ai-video-{uuid4().hex[:8]}.mp4", "video/mp4", data
        )
        final_video = self.assets.projects_dir / project.id / "video-vertical.mp4"
        final_video.parent.mkdir(parents=True, exist_ok=True)
        final_video.write_bytes(data)
        return self.assets.approve_rights(asset.id, True)

    @staticmethod
    def _font(size: int):
        path = Path("C:/Windows/Fonts/malgun.ttf")
        return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()

    @classmethod
    def _demo_image(cls, product_name: str, prompt: str) -> bytes:
        image = Image.new("RGB", (720, 1280), "#5a35d6")
        draw = ImageDraw.Draw(image)
        draw.ellipse((60, 150, 660, 750), fill="#f6b4c8")
        draw.rounded_rectangle((70, 830, 650, 1120), 28, fill="#181322")
        draw.text((360, 885), product_name, font=cls._font(52), fill="white", anchor="ma")
        summary = prompt.strip()[:34] or "AI 제품 이미지 데모"
        draw.text((360, 995), summary, font=cls._font(25), fill="#e5ddff", anchor="ma")
        draw.text((360, 1195), "HIGGSFIELD 연결 전 데모", font=cls._font(20), fill="white", anchor="ma")
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    def _demo_video(self, start_image: Path) -> bytes:
        with TemporaryDirectory(prefix="clipflow-ai-") as directory:
            output = Path(directory) / "clip.mp4"
            subprocess.run(
                [
                    self.ffmpeg_path, "-y", "-loop", "1", "-i", str(start_image),
                    "-t", "5", "-vf",
                    "scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
                    "zoompan=z='min(zoom+0.0008,1.06)':d=125:s=360x640:fps=25,format=yuv420p",
                    "-an", "-c:v", "libx264", "-movflags", "+faststart", str(output),
                ],
                shell=False,
                check=True,
                timeout=120,
                capture_output=True,
            )
            return output.read_bytes()

    @staticmethod
    def _download(url: str, expected_type: str) -> bytes:
        request = Request(url, headers={"User-Agent": "ClipFlow/1.0"})
        with urlopen(request, timeout=120) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith(expected_type):
                raise ValueError("Higgsfield 결과 파일 형식이 올바르지 않습니다.")
            data = response.read(MAX_FILE_BYTES + 1)
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("Higgsfield 결과 파일 크기가 허용 범위를 벗어났습니다.")
        return data
