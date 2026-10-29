import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipflow.providers.higgsfield import (
    HiggsfieldCLI,
    HiggsfieldNotAvailable,
    VideoGenerationOptions,
)


class RecordingRunner:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.commands: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        payload = self.responses.pop(0)
        if hasattr(payload, "returncode"):
            return payload
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def test_unavailable_cli_reports_setup_without_running_command():
    provider = HiggsfieldCLI(executable=None)

    status = provider.status()

    assert status == {
        "available": False,
        "authenticated": False,
        "message": "Higgsfield CLI를 설치하고 로그인하세요.",
    }
    with pytest.raises(HiggsfieldNotAvailable):
        provider.estimate_video("slow product camera move", None)


def test_status_uses_current_cli_auth_and_workspace_commands():
    runner = RecordingRunner([
        SimpleNamespace(returncode=0, stdout="test-token", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"id": "workspace-1", "name": "My workspace"}),
            stderr="",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"credits": 120, "subscription_plan_type": "ultra"}),
            stderr="",
        ),
    ])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    status = provider.status()

    assert status == {
        "available": True,
        "authenticated": True,
        "workspace_selected": True,
        "workspace": {"id": "workspace-1", "name": "My workspace"},
        "credits": 120,
        "plan": "ultra",
        "message": "Higgsfield 연결됨 · 크레딧 120",
    }
    assert runner.commands == [
        ["higgsfield", "auth", "token"],
        ["higgsfield", "workspace", "status", "--json"],
        ["higgsfield", "account", "status", "--json"],
    ]


def test_status_reports_login_needed_without_parsing_error():
    runner = RecordingRunner([
        SimpleNamespace(returncode=1, stdout="", stderr="Error: Not authenticated"),
    ])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    status = provider.status()

    assert status == {
        "available": True,
        "authenticated": False,
        "workspace_selected": False,
        "message": "Higgsfield 로그인이 필요합니다.",
    }


def test_video_cost_uses_higgsfield_web_defaults(tmp_path):
    start_image = tmp_path / "product.png"
    start_image.write_bytes(b"image")
    runner = RecordingRunner([{"credits": 18}])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    result = provider.estimate_video(
        "slow product camera move", start_image, VideoGenerationOptions()
    )

    assert result["credits"] == 18
    assert runner.commands == [[
        "higgsfield", "generate", "cost", "seedance_2_0",
        "--prompt", "slow product camera move", "--aspect_ratio", "auto",
        "--duration", "8", "--resolution", "720p", "--mode", "std",
        "--bitrate_mode", "high", "--genre", "auto", "--generate_audio", "true",
        "--start-image", str(start_image),
        "--json",
    ]]


def test_video_cost_uses_user_selected_higgsfield_options():
    runner = RecordingRunner([{"credits": 90}])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)
    options = VideoGenerationOptions(
        duration=5,
        aspect_ratio="9:16",
        resolution="1080p",
        bitrate_mode="standard",
        generate_audio=False,
    )

    provider.estimate_video("한국어 대사가 있는 장면", None, options)

    assert runner.commands == [[
        "higgsfield", "generate", "cost", "seedance_2_0",
        "--prompt", "한국어 대사가 있는 장면", "--aspect_ratio", "9:16",
        "--duration", "5", "--resolution", "1080p", "--mode", "std",
        "--bitrate_mode", "standard", "--genre", "auto",
        "--generate_audio", "false", "--json",
    ]]


def test_windows_npm_cmd_uses_node_cli_entrypoint_for_json_output(
    tmp_path, monkeypatch
):
    npm_dir = tmp_path / "npm"
    wrapper = npm_dir / "higgsfield.CMD"
    node = tmp_path / "node.exe"
    script = npm_dir / "node_modules" / "@higgsfield" / "cli" / "bin" / "higgsfield.js"
    script.parent.mkdir(parents=True)
    wrapper.write_text("wrapper", encoding="utf-8")
    node.write_bytes(b"node")
    script.write_text("cli", encoding="utf-8")

    def fake_which(name):
        return str(wrapper) if name == "higgsfield" else str(node) if name == "node" else None

    monkeypatch.setattr("clipflow.providers.higgsfield.shutil.which", fake_which)
    runner = RecordingRunner([{"credits": 5}])
    provider = HiggsfieldCLI(runner=runner)

    assert provider.estimate_video("test prompt", None)["credits"] == 5
    assert runner.commands[0][:2] == [str(node), str(script)]


def test_cli_output_is_decoded_as_utf8_on_windows():
    runner = RecordingRunner([{"credits": 5}])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    provider.estimate_video("test prompt", None)

    assert runner.kwargs[0]["encoding"] == "utf-8"
    assert runner.kwargs[0]["errors"] == "replace"


def test_generate_video_returns_job_and_download_url(tmp_path):
    start_image = tmp_path / "product.png"
    start_image.write_bytes(b"image")
    runner = RecordingRunner([
        {"id": "job-123", "status": "completed", "results": [{"url": "https://cdn.example/clip.mp4"}]}
    ])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    result = provider.generate_video("gentle orbit around the product", start_image)

    assert result == {"job_id": "job-123", "url": "https://cdn.example/clip.mp4"}
    assert "--wait" in runner.commands[0]
    assert runner.commands[0][-1] == "--json"


def test_generate_video_uses_last_json_object_from_cli_progress(tmp_path):
    start_image = tmp_path / "product.png"
    start_image.write_bytes(b"image")
    stdout = "\n".join([
        json.dumps({"id": "job-123", "status": "queued"}, indent=2),
        json.dumps({
            "id": "job-123",
            "status": "completed",
            "result_url": "https://cdn.example/final.mp4",
        }, indent=2),
    ])
    runner = RecordingRunner([
        SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    ])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    result = provider.generate_video("gentle movement", start_image)

    assert result == {
        "job_id": "job-123",
        "url": "https://cdn.example/final.mp4",
    }


def test_generate_image_uses_vertical_lite_model():
    runner = RecordingRunner([
        {"job_id": "image-1", "output": {"url": "https://cdn.example/product.png"}}
    ])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    result = provider.generate_image("studio product photo")

    assert result == {"job_id": "image-1", "url": "https://cdn.example/product.png"}
    assert runner.commands[0][:4] == ["higgsfield", "generate", "create", "nano_banana_2_lite"]
    assert "9:16" in runner.commands[0]


def test_image_cost_uses_vertical_lite_model():
    runner = RecordingRunner([{"credits": 4}])
    provider = HiggsfieldCLI(executable="higgsfield", runner=runner)

    result = provider.estimate_image("studio product photo")

    assert result["credits"] == 4
    assert runner.commands == [[
        "higgsfield", "generate", "cost", "nano_banana_2_lite",
        "--prompt", "studio product photo", "--aspect_ratio", "9:16",
        "--resolution", "1k", "--json",
    ]]
