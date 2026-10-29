import base64
import wave

import pytest

from clipflow.app import Services
from clipflow.config import Settings
from clipflow.providers.gemini_speech import GeminiSpeechProvider


@pytest.mark.asyncio
async def test_gemini_speech_writes_24khz_mono_wav_and_uses_korean_voice(tmp_path):
    pcm = b"\x00\x00" * 24_000
    captured = {}

    def post(url, headers, payload):
        captured.update(url=url, headers=headers, payload=payload)
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/L16;codec=pcm;rate=24000",
                                    "data": base64.b64encode(pcm).decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    output = tmp_path / "speech.wav"
    result = await GeminiSpeechProvider(
        "secret-key",
        model="gemini-3.1-flash-tts-preview",
        json_post=post,
    ).generate("복숭아가 달콤해 보여요.", output, "Kore", "밝고 자연스럽게")

    with wave.open(str(output), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 24_000
        assert source.getnframes() == 24_000
    assert result.duration_seconds == 1.0
    assert result.provider == "gemini"
    assert result.model == "gemini-3.1-flash-tts-preview"
    assert captured["url"].endswith(
        "/gemini-3.1-flash-tts-preview:generateContent"
    )
    generation = captured["payload"]["generationConfig"]
    assert generation["responseModalities"] == ["AUDIO"]
    assert generation["speechConfig"]["languageCode"] == "ko-KR"
    assert (
        generation["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
        == "Kore"
    )
    prompt = captured["payload"]["contents"][0]["parts"][0]["text"]
    assert "복숭아가 달콤해 보여요." in prompt
    assert "밝고 자연스럽게" in prompt


@pytest.mark.asyncio
async def test_gemini_speech_rejects_empty_script(tmp_path):
    provider = GeminiSpeechProvider("secret-key", json_post=lambda *_: {})

    with pytest.raises(ValueError, match="대본이 비어"):
        await provider.generate("   ", tmp_path / "speech.wav", "Kore", "자연스럽게")


def test_services_use_gemini_speech_when_only_gemini_key_is_set(tmp_path):
    services = Services(
        Settings(base_dir=tmp_path, offline=False, gemini_api_key="gemini-key")
    )

    assert isinstance(services.speech_provider, GeminiSpeechProvider)
    assert services.speech_voice == "Kore"
