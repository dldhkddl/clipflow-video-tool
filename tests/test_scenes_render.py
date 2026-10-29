import asyncio
import subprocess
from io import BytesIO

import imageio_ffmpeg
import pytest
from PIL import Image, ImageChops, ImageStat

import clipflow.media.inspect as inspect_module
from clipflow.media.inspect import inspect_video
from clipflow.media.render import RenderRequest, RenderService
from clipflow.media.scenes import build_timeline
from clipflow.models import SceneDraft, ScriptDraft
from clipflow.providers.fake import FakeSpeechProvider


def script(seconds=3.0):
    part = seconds / 4
    return ScriptDraft(
        hook="테스트 훅",
        selection_criteria=["기준"],
        pros=["장점"],
        cons=["단점"],
        cta="설명란 확인",
        scenes=[
            SceneDraft(order=i, narration=f"장면 {i}", on_screen_text=f"장면 {i}", duration_seconds=part)
            for i in range(1, 5)
        ],
    )


def test_timeline_fits_target_within_two_seconds():
    timeline = build_timeline(script(30), speech_seconds=28.4, target_seconds=30)
    assert 28 <= timeline.total_seconds <= 32
    assert all(scene.start < scene.end for scene in timeline.scenes)


def test_timeline_keeps_scene_asset_selection_and_render_resolves_it(tmp_path):
    draft = script(4)
    draft.scenes[0] = draft.scenes[0].model_copy(update={"asset_id": "second"})
    timeline = build_timeline(draft, speech_seconds=4, target_seconds=4)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    sources = RenderService.resolve_scene_sources(
        timeline,
        fallback_paths=[first, second],
        asset_paths_by_id={"first": first, "second": second},
    )

    assert timeline.scenes[0].asset_id == "second"
    assert sources[0] == second
    assert sources[1] == second


def test_final_render_contract(tmp_path):
    request = RenderRequest(
        script=script(3),
        asset_paths=[tmp_path / "sample.png"],
        speech_path=tmp_path / "speech.wav",
        output_path=tmp_path / "output.mp4",
        target_seconds=3,
    )
    command = RenderService().build_encode_command(
        tmp_path / "concat.txt", request, preview=False
    )
    joined = " ".join(str(item) for item in command)
    assert "1080:1920" in joined
    assert "libx264" in joined
    assert "aac" in joined
    assert "30" in joined


def test_outline_caption_keeps_image_bright_without_a_black_panel():
    service = RenderService()
    source = Image.new("RGB", (360, 640), "white")

    frame = service._compose_frame(
        source,
        (360, 640),
        "복숭아 향이 진해요",
        show_disclosure=False,
    )
    overlay = service._compose_overlay(
        (360, 640),
        "복숭아 향이 진해요",
        show_disclosure=False,
    )

    assert frame.getpixel((10, 10)) == (255, 255, 255)
    assert min(frame.getpixel((28, 435))) >= 240
    assert overlay.getpixel((10, 10))[3] == 0


def test_preview_render_creates_video_with_audio(tmp_path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (640, 640), "purple").save(image_path)
    speech = asyncio.run(
        FakeSpeechProvider(seconds=3).generate(
            "테스트", tmp_path / "speech.wav", "coral", "차분하게"
        )
    )
    request = RenderRequest(
        script=script(3),
        asset_paths=[image_path],
        speech_path=speech.path,
        output_path=tmp_path / "preview.mp4",
        target_seconds=3,
    )
    result = RenderService().render(request, preview=True)
    metadata = inspect_video(result.path)
    assert metadata.width == 360
    assert metadata.height == 640
    assert metadata.has_audio is True
    assert 2.5 <= metadata.duration_seconds <= 3.5


def test_preview_render_preserves_motion_from_uploaded_mp4(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / "moving.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=12",
            "-t", "3", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )
    speech = asyncio.run(
        FakeSpeechProvider(seconds=3).generate(
            "테스트", tmp_path / "speech.wav", "coral", "차분하게"
        )
    )
    output = tmp_path / "motion-preview.mp4"
    RenderService().render(
        RenderRequest(
            script=script(3),
            asset_paths=[source],
            speech_path=speech.path,
            output_path=output,
            target_seconds=3,
        ),
        preview=True,
    )
    frames = []
    for index, second in enumerate((0.15, 0.55)):
        frame = tmp_path / f"motion-{index}.png"
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(second), "-i", str(output), "-frames:v", "1", str(frame)],
            check=True,
            capture_output=True,
        )
        frames.append(Image.open(frame).convert("RGB"))

    difference = ImageChops.difference(frames[0], frames[1])
    assert sum(ImageStat.Stat(difference).mean) > 5


def test_video_inspection_stops_after_first_frame(tmp_path, monkeypatch):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / "long.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x284:r=1",
            "-t", "30", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )
    real_run = inspect_module.subprocess.run
    captured = {}

    def capture(command, **kwargs):
        captured["command"] = command
        return real_run(command, **kwargs)

    monkeypatch.setattr(inspect_module.subprocess, "run", capture)
    metadata = inspect_video(source)

    assert metadata.format_name
    assert "-frames:v" in captured["command"]
    assert captured["command"][captured["command"].index("-frames:v") + 1] == "1"


def test_video_inspection_converts_probe_timeout_to_readable_error(tmp_path, monkeypatch):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / "valid.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=160x284:d=0.2",
            "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 15)

    monkeypatch.setattr(inspect_module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="메타데이터"):
        inspect_video(source)
