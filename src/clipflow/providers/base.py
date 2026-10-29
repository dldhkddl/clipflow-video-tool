from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..models import ScriptDraft, ScriptRequest


class SpeechResult(BaseModel):
    path: Path
    duration_seconds: float
    provider: str
    model: str
    estimated_krw: int = 0
    ai_generated: bool = True


class ScriptProvider(Protocol):
    async def generate(self, request: ScriptRequest) -> ScriptDraft: ...


class SpeechProvider(Protocol):
    async def generate(
        self, text: str, output_path: Path, voice: str, instructions: str
    ) -> SpeechResult: ...

