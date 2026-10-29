import asyncio
import hashlib
import wave
from pathlib import Path

from openai import OpenAI

from .base import SpeechResult


def speech_input_hash(text: str, voice: str, instructions: str) -> str:
    normalized = "\n".join((text.strip(), voice.strip(), instructions.strip()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class OpenAISpeechProvider:
    def __init__(self, api_key: str, model: str, provider_name: str = "openai"):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.provider_name = provider_name

    async def generate(
        self, text: str, output_path: Path, voice: str, instructions: str
    ) -> SpeechResult:
        if not text.strip():
            raise ValueError("음성으로 만들 대본이 비어 있습니다.")
        output_path = output_path.with_suffix(".wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.wav")

        def request_audio() -> None:
            try:
                with self.client.audio.speech.with_streaming_response.create(
                    model=self.model,
                    voice=voice,
                    input=text,
                    instructions=instructions,
                    response_format="wav",
                ) as response:
                    response.stream_to_file(temporary)
                if not temporary.exists() or temporary.stat().st_size == 0:
                    raise RuntimeError("음성 API가 빈 파일을 반환했습니다.")
                temporary.replace(output_path)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(request_audio)
        try:
            with wave.open(str(output_path), "rb") as source:
                duration = source.getnframes() / source.getframerate()
        except wave.Error as exc:
            output_path.unlink(missing_ok=True)
            raise RuntimeError("생성된 음성 파일을 읽을 수 없습니다.") from exc
        return SpeechResult(
            path=output_path,
            duration_seconds=duration,
            provider=self.provider_name,
            model=self.model,
        )
