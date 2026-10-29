import pytest

from clipflow.models import AI_VOICE_DISCLOSURE
from clipflow.providers.fake import FakeSpeechProvider
from clipflow.providers.openai_speech import speech_input_hash


@pytest.mark.asyncio
async def test_fake_speech_writes_audio_and_requires_ai_disclosure(tmp_path):
    result = await FakeSpeechProvider(seconds=1.25).generate(
        "테스트 대본", tmp_path / "speech.wav", "coral", "차분하게"
    )
    assert result.path.exists()
    assert result.duration_seconds == 1.25
    assert result.ai_generated is True
    assert "AI 음성" in AI_VOICE_DISCLOSURE


def test_unchanged_scene_reuses_audio_hash():
    first = speech_input_hash("같은 대본", "coral", "차분하게")
    second = speech_input_hash("같은 대본", "coral", "차분하게")
    changed = speech_input_hash("바뀐 대본", "coral", "차분하게")
    assert first == second
    assert first != changed
