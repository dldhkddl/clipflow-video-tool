import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from pydantic import BaseModel


class VideoMetadata(BaseModel):
    width: int
    height: int
    fps: float
    duration_seconds: float
    has_audio: bool
    format_name: str


def inspect_video(path: Path) -> VideoMetadata:
    if not path.exists():
        raise FileNotFoundError(path)
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
    metadata = next(reader)
    reader.close()
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command, shell=False, timeout=15, capture_output=True
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("영상 메타데이터 확인 시간이 초과되었습니다.") from exc
    stderr = completed.stderr.decode("utf-8", errors="replace")
    format_match = re.search(r"Input #0,\s*(.+?),\s*from\s", stderr)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    else:
        duration = float(metadata.get("duration") or 0)
    width, height = metadata["size"]
    return VideoMetadata(
        width=width,
        height=height,
        fps=float(metadata.get("fps") or 0),
        duration_seconds=duration,
        has_audio="Audio:" in stderr,
        format_name=format_match.group(1).strip() if format_match else "",
    )
