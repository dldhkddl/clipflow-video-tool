import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal


class HiggsfieldError(RuntimeError):
    pass


class HiggsfieldNotAvailable(HiggsfieldError):
    pass


_AUTO = object()


@dataclass(frozen=True)
class VideoGenerationOptions:
    model: Literal["seedance_2_0"] = "seedance_2_0"
    duration: Literal[5, 8] = 8
    aspect_ratio: Literal["auto", "9:16", "16:9"] = "auto"
    resolution: Literal["720p", "1080p"] = "720p"
    bitrate_mode: Literal["standard", "high"] = "high"
    generate_audio: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


class HiggsfieldCLI:
    def __init__(
        self,
        executable: str | None | object = _AUTO,
        runner: Callable[..., Any] = subprocess.run,
    ):
        self.executable = shutil.which("higgsfield") if executable is _AUTO else executable
        self.command_prefix = self._resolve_command_prefix(self.executable)
        self.runner = runner

    @staticmethod
    def _resolve_command_prefix(executable: str | None | object) -> list[str]:
        if not executable:
            return []
        path = Path(str(executable))
        if path.suffix.lower() == ".cmd":
            node = shutil.which("node")
            script = (
                path.parent
                / "node_modules"
                / "@higgsfield"
                / "cli"
                / "bin"
                / "higgsfield.js"
            )
            if node and script.is_file():
                return [str(node), str(script)]
        return [str(executable)]

    def status(self) -> dict:
        if not self.executable:
            return {
                "available": False,
                "authenticated": False,
                "message": "Higgsfield CLI를 설치하고 로그인하세요.",
            }

        auth = self.runner(
            [*self.command_prefix, "auth", "token"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if auth.returncode != 0:
            return {
                "available": True,
                "authenticated": False,
                "workspace_selected": False,
                "message": "Higgsfield 로그인이 필요합니다.",
            }

        workspace_result = self.runner(
            [*self.command_prefix, "workspace", "status", "--json"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if workspace_result.returncode != 0:
            return {
                "available": True,
                "authenticated": True,
                "workspace_selected": False,
                "message": "Higgsfield 작업 공간을 선택하세요.",
            }

        workspace = self._json(workspace_result.stdout)
        account_result = self.runner(
            [*self.command_prefix, "account", "status", "--json"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        status = {
            "available": True,
            "authenticated": True,
            "workspace_selected": True,
            "workspace": workspace,
        }
        if account_result.returncode != 0:
            status["message"] = "Higgsfield 연결됨"
            return status

        account = self._json(account_result.stdout)
        credits = account.get("credits") or account.get("available_credits")
        plan = (
            account.get("plan")
            or account.get("subscription")
            or account.get("subscription_plan_type")
        )
        if credits is not None:
            status["credits"] = credits
        if plan is not None:
            status["plan"] = plan
        status["message"] = (
            f"Higgsfield 연결됨 · 크레딧 {credits}"
            if credits is not None
            else "Higgsfield 연결됨"
        )
        return status

    def estimate_video(
        self,
        prompt: str,
        start_image: Path | None,
        options: VideoGenerationOptions | None = None,
    ) -> dict:
        command = self._video_command(
            "cost", prompt, start_image, options or VideoGenerationOptions()
        )
        payload = self._run(command, timeout=60)
        credits = payload.get("credits") or payload.get("credit_cost") or payload.get("cost")
        return {"credits": credits, "raw": payload}

    def generate_video(
        self,
        prompt: str,
        start_image: Path | None,
        options: VideoGenerationOptions | None = None,
    ) -> dict:
        command = self._video_command(
            "create", prompt, start_image, options or VideoGenerationOptions()
        )
        command.insert(-1, "--wait")
        return self._result(self._run(command, timeout=1800))

    def generate_image(self, prompt: str) -> dict:
        command = [
            *self._require_command(), "generate", "create", "nano_banana_2_lite",
            "--prompt", prompt, "--aspect_ratio", "9:16", "--resolution", "1k",
            "--wait", "--json",
        ]
        return self._result(self._run(command, timeout=600))

    def estimate_image(self, prompt: str) -> dict:
        command = [
            *self._require_command(), "generate", "cost", "nano_banana_2_lite",
            "--prompt", prompt, "--aspect_ratio", "9:16", "--resolution", "1k",
            "--json",
        ]
        payload = self._run(command, timeout=60)
        credits = payload.get("credits") or payload.get("credit_cost") or payload.get("cost")
        return {"credits": credits, "raw": payload}

    def _video_command(
        self,
        action: str,
        prompt: str,
        start_image: Path | None,
        options: VideoGenerationOptions,
    ) -> list[str]:
        command = [
            *self._require_command(), "generate", action, options.model,
            "--prompt", prompt,
            "--aspect_ratio", options.aspect_ratio,
            "--duration", str(options.duration),
            "--resolution", options.resolution,
            "--mode", "std",
            "--bitrate_mode", options.bitrate_mode,
            "--genre", "auto",
            "--generate_audio", str(options.generate_audio).lower(),
        ]
        if start_image is not None:
            command.extend(["--start-image", str(start_image)])
        command.append("--json")
        return command

    def _require_executable(self) -> str:
        if not self.executable:
            raise HiggsfieldNotAvailable("Higgsfield CLI를 설치하고 로그인하세요.")
        return str(self.executable)

    def _require_command(self) -> list[str]:
        self._require_executable()
        return list(self.command_prefix)

    def _run(self, command: list[str], timeout: int) -> dict:
        self._require_executable()
        completed = self.runner(
            command,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Higgsfield 생성 요청에 실패했습니다."
            raise HiggsfieldError(message)
        return self._json(completed.stdout)

    @staticmethod
    def _json(output: str) -> dict:
        for line in reversed(output.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        decoder = json.JSONDecoder()
        objects = []
        index = 0
        while index < len(output):
            if output[index] != "{":
                index += 1
                continue
            try:
                value, end = decoder.raw_decode(output, index)
            except json.JSONDecodeError:
                index += 1
                continue
            if isinstance(value, dict):
                objects.append(value)
            index = end
        if objects:
            return objects[-1]
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise HiggsfieldError("Higgsfield 응답을 해석할 수 없습니다.") from exc
        if not isinstance(value, dict):
            raise HiggsfieldError("Higgsfield 응답 형식이 올바르지 않습니다.")
        return value

    @classmethod
    def _result(cls, payload: dict) -> dict:
        url = cls._find_url(payload)
        if not url:
            raise HiggsfieldError("Higgsfield 결과 URL이 없습니다.")
        job_id = payload.get("job_id") or payload.get("id")
        return {"job_id": job_id, "url": url}

    @classmethod
    def _find_url(cls, value: Any) -> str | None:
        if isinstance(value, dict):
            for key in ("url", "result_url", "output_url", "download_url"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
            for nested in value.values():
                found = cls._find_url(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_url(nested)
                if found:
                    return found
        return None
