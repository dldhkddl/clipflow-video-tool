from pydantic import BaseModel

from ..models import ScriptDraft


class TimelineScene(BaseModel):
    order: int
    start: float
    end: float
    narration: str
    on_screen_text: str
    asset_id: str | None = None


class Timeline(BaseModel):
    scenes: list[TimelineScene]
    total_seconds: float


def build_timeline(
    script: ScriptDraft, speech_seconds: float, target_seconds: int
) -> Timeline:
    if speech_seconds > target_seconds + 2:
        raise ValueError("음성이 선택한 영상 길이보다 너무 깁니다.")
    weights = [scene.duration_seconds for scene in script.scenes]
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("장면 길이 합계가 0입니다.")
    cursor = 0.0
    scenes: list[TimelineScene] = []
    for index, (scene, weight) in enumerate(zip(script.scenes, weights, strict=True)):
        duration = target_seconds * weight / total_weight
        end = target_seconds if index == len(script.scenes) - 1 else cursor + duration
        scenes.append(
            TimelineScene(
                order=scene.order,
                start=round(cursor, 3),
                end=round(end, 3),
                narration=scene.narration,
                on_screen_text=scene.on_screen_text,
                asset_id=scene.asset_id,
            )
        )
        cursor = end
    return Timeline(scenes=scenes, total_seconds=float(target_seconds))
