import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field

from ..models import AFFILIATE_DISCLOSURE, ScriptDraft
from .scenes import Timeline, build_timeline


class RenderRequest(BaseModel):
    script: ScriptDraft
    asset_paths: list[Path]
    asset_paths_by_id: dict[str, Path] = Field(default_factory=dict)
    speech_path: Path
    output_path: Path
    target_seconds: int


class RenderResult(BaseModel):
    path: Path
    preview: bool


class RenderService:
    def __init__(self, ffmpeg_path: str | None = None):
        self.ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()

    def build_encode_command(
        self, concat_path: Path, request: RenderRequest, preview: bool
    ) -> list[str]:
        width, height, fps, crf = (360, 640, 24, 30) if preview else (1080, 1920, 30, 20)
        return [
            self.ffmpeg_path,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-i", str(request.speech_path),
            "-vf", f"scale={width}:{height},fps={fps},format=yuv420p",
            "-c:v", "libx264",
            "-crf", str(crf),
            "-c:a", "aac",
            "-ar", "48000",
            "-shortest",
            "-movflags", "+faststart",
            str(request.output_path),
        ]

    def render(self, request: RenderRequest, preview: bool = False) -> RenderResult:
        if not request.asset_paths:
            raise ValueError("렌더링할 이미지 또는 영상이 필요합니다.")
        if not request.speech_path.exists():
            raise FileNotFoundError(request.speech_path)
        timeline = build_timeline(
            request.script,
            speech_seconds=float(request.target_seconds),
            target_seconds=request.target_seconds,
        )
        output = request.output_path
        output.parent.mkdir(parents=True, exist_ok=True)
        work = output.parent / f".render-{uuid4().hex}"
        work.mkdir(parents=True)
        temporary_output = work / "output.mp4"
        try:
            concat_path = self._create_scene_clips(
                work,
                self.resolve_scene_sources(
                    timeline,
                    request.asset_paths,
                    request.asset_paths_by_id,
                ),
                timeline,
                preview=preview,
            )
            run_request = request.model_copy(update={"output_path": temporary_output})
            command = self.build_encode_command(concat_path, run_request, preview)
            subprocess.run(
                command,
                shell=False,
                timeout=300,
                check=True,
                capture_output=True,
            )
            if not temporary_output.exists() or temporary_output.stat().st_size == 0:
                raise RuntimeError("렌더링 결과가 비어 있습니다.")
            temporary_output.replace(output)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return RenderResult(path=output, preview=preview)

    def _create_scene_clips(
        self,
        work: Path,
        asset_paths: list[Path],
        timeline: Timeline,
        *,
        preview: bool,
    ) -> Path:
        size = (360, 640) if preview else (1080, 1920)
        entries: list[str] = []
        for index, scene in enumerate(timeline.scenes):
            source = asset_paths[index]
            clip = self._build_scene_clip(
                work,
                source,
                index,
                scene.end - scene.start,
                size,
                scene.on_screen_text,
                show_disclosure=index == len(timeline.scenes) - 1,
                preview=preview,
            )
            entries.append(f"file '{clip.as_posix()}'")
        concat_path = work / "concat.txt"
        concat_path.write_text("\n".join(entries), encoding="utf-8")
        return concat_path

    @staticmethod
    def resolve_scene_sources(
        timeline: Timeline,
        fallback_paths: list[Path],
        asset_paths_by_id: dict[str, Path],
    ) -> list[Path]:
        if not fallback_paths:
            raise ValueError("렌더링할 이미지 또는 영상이 필요합니다.")
        sources: list[Path] = []
        for index, scene in enumerate(timeline.scenes):
            if scene.asset_id:
                source = asset_paths_by_id.get(scene.asset_id)
                if source is None:
                    raise ValueError(f"장면 {index + 1}에 선택한 자료를 사용할 수 없습니다.")
                sources.append(source)
            else:
                sources.append(fallback_paths[index % len(fallback_paths)])
        return sources

    def _build_scene_clip(
        self,
        work: Path,
        source: Path,
        index: int,
        duration: float,
        size: tuple[int, int],
        text: str,
        *,
        show_disclosure: bool,
        preview: bool,
    ) -> Path:
        width, height = size
        fps = 24 if preview else 30
        crf = 30 if preview else 20
        output = work / f"scene-{index:03d}.mp4"
        if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            image = self._load_visual(source, work, index)
            frame = self._compose_frame(
                image,
                size,
                text,
                show_disclosure=show_disclosure,
            )
            frame_path = work / f"frame-{index:03d}.png"
            frame.save(frame_path, "PNG")
            command = [
                self.ffmpeg_path,
                "-y",
                "-loop", "1",
                "-i", str(frame_path),
                "-t", f"{duration:.3f}",
                "-vf", f"fps={fps},format=yuv420p",
                "-an",
                "-c:v", "libx264",
                "-crf", str(crf),
                str(output),
            ]
        else:
            overlay = self._compose_overlay(
                size,
                text,
                show_disclosure=show_disclosure,
            )
            overlay_path = work / f"overlay-{index:03d}.png"
            overlay.save(overlay_path, "PNG")
            video_filter = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1[base];"
                f"[base][1:v]overlay=0:0:format=auto,fps={fps},format=yuv420p[v]"
            )
            command = [
                self.ffmpeg_path,
                "-y",
                "-stream_loop", "-1",
                "-i", str(source),
                "-loop", "1",
                "-i", str(overlay_path),
                "-t", f"{duration:.3f}",
                "-filter_complex", video_filter,
                "-map", "[v]",
                "-an",
                "-c:v", "libx264",
                "-crf", str(crf),
                str(output),
            ]
        subprocess.run(
            command,
            shell=False,
            timeout=180,
            check=True,
            capture_output=True,
        )
        return output

    def _load_visual(self, source: Path, work: Path, index: int) -> Image.Image:
        if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            with Image.open(source) as image:
                return image.convert("RGB")
        extracted = work / f"video-source-{index:03d}.png"
        subprocess.run(
            [self.ffmpeg_path, "-y", "-ss", "0", "-i", str(source), "-frames:v", "1", str(extracted)],
            shell=False,
            timeout=60,
            check=True,
            capture_output=True,
        )
        with Image.open(extracted) as image:
            return image.convert("RGB")

    @staticmethod
    def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = (
            [Path("C:/Windows/Fonts/malgunbd.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")]
            if bold
            else [Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/arial.ttf")]
        )
        for candidate in candidates:
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
        return ImageFont.load_default()

    def _compose_frame(
        self,
        source: Image.Image,
        size: tuple[int, int],
        text: str,
        *,
        show_disclosure: bool,
    ) -> Image.Image:
        background = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(background, "RGBA")
        self._draw_outline_caption(draw, size, text, show_disclosure=show_disclosure)
        return background

    def _compose_overlay(
        self,
        size: tuple[int, int],
        text: str,
        *,
        show_disclosure: bool,
    ) -> Image.Image:
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        self._draw_outline_caption(draw, size, text, show_disclosure=show_disclosure)
        return overlay

    def _draw_outline_caption(
        self,
        draw: ImageDraw.ImageDraw,
        size: tuple[int, int],
        text: str,
        *,
        show_disclosure: bool,
    ) -> None:
        width, height = size
        margin = int(width * 0.07)
        font = self._font(max(22, int(width * 0.075)), bold=True)
        wrapped = self._wrap(draw, text, font, width - margin * 2)
        box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=8)
        text_height = box[3] - box[1]
        y = int(height * 0.70) - text_height / 2
        stroke = max(2, int(width * 0.008))
        shadow = max(2, int(width * 0.006))
        draw.multiline_text(
            (width / 2 + shadow, y + shadow),
            wrapped,
            font=font,
            fill=(33, 25, 20, 150),
            anchor="ma",
            align="center",
            spacing=8,
            stroke_width=stroke,
            stroke_fill=(33, 25, 20, 150),
        )
        draw.multiline_text(
            (width / 2, y),
            wrapped,
            font=font,
            fill=(255, 253, 248, 255),
            anchor="ma",
            align="center",
            spacing=8,
            stroke_width=stroke,
            stroke_fill=(33, 25, 20, 255),
        )
        if show_disclosure:
            small = self._font(max(12, int(width * 0.026)))
            disclosure = self._wrap(draw, AFFILIATE_DISCLOSURE, small, width - margin * 2)
            draw.multiline_text(
                (width / 2, height - margin * 1.5),
                disclosure,
                font=small,
                fill="white",
                anchor="ms",
                align="center",
                spacing=4,
                stroke_width=max(1, int(width * 0.003)),
                stroke_fill=(33, 25, 20, 255),
            )

    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        lines: list[str] = []
        current = ""
        for char in text.strip():
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines[:3])
