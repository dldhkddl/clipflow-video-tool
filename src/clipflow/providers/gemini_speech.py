import asyncio
import base64
import binascii
import wave
from pathlib import Path

from .base import SpeechResult
from .gemini import GeminiClient, GeminiError, JsonPost


class GeminiSpeechProvider:
    SAMPLE_RATE = 24_000

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-tts-preview",
        *,
        json_post: JsonPost | None = None,
    ):
        self.api_key = api_key.strip()
        self.model = model.removeprefix("models/").strip()
        self.json_post = json_post or GeminiClient._default_json_post

    async def generate(
        self, text: str, output_path: Path, voice: str, instructions: str
    ) -> SpeechResult:
        if not text.strip():
            raise ValueError("음성으로 만들 대본이 비어 있습니다.")
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "한국어 음성을 합성하세요. 지시사항은 소리 내어 읽지 마세요.\n"
                                f"말하기 방식: {instructions.strip()}\n"
                                f"실제 낭독 대본:\n{text.strip()}"
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "languageCode": "ko-KR",
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice.strip()}
                    },
                },
            },
            "model": self.model,
        }
        response = await asyncio.to_thread(
            self.json_post,
            f"{GeminiClient.API_ROOT}/{self.model}:generateContent",
            {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            payload,
        )
        pcm = self._audio_bytes(response)
        if not pcm or len(pcm) % 2:
            raise GeminiError("Gemini 음성 데이터가 올바르지 않습니다.")

        output_path = output_path.with_suffix(".wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.wav")
        try:
            with wave.open(str(temporary), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(self.SAMPLE_RATE)
                target.writeframes(pcm)
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)

        duration = len(pcm) / (self.SAMPLE_RATE * 2)
        return SpeechResult(
            path=output_path,
            duration_seconds=duration,
            provider="gemini",
            model=self.model,
        )

    @staticmethod
    def _audio_bytes(response: dict) -> bytes:
        try:
            parts = response["candidates"][0]["content"]["parts"]
            inline = next(
                part.get("inlineData") or part.get("inline_data")
                for part in parts
                if part.get("inlineData") or part.get("inline_data")
            )
            mime_type = inline.get("mimeType") or inline.get("mime_type") or ""
            if not mime_type.startswith("audio/"):
                raise GeminiError("Gemini가 음성 형식이 아닌 데이터를 반환했습니다.")
            return base64.b64decode(inline["data"], validate=True)
        except GeminiError:
            raise
        except (KeyError, IndexError, StopIteration, TypeError, binascii.Error) as exc:
            raise GeminiError("Gemini가 음성 데이터를 반환하지 못했습니다.") from exc
