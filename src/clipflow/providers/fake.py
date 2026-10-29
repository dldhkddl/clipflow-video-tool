import wave
from pathlib import Path

from ..models import AFFILIATE_DISCLOSURE, SceneDraft, ScriptDraft, ScriptRequest
from .base import SpeechResult


class FakeScriptProvider:
    async def generate(self, request: ScriptRequest) -> ScriptDraft:
        part = request.target_seconds / 5
        analysis = request.analysis
        points = (
            [item.text for item in analysis.promotion_points]
            if analysis and analysis.promotion_points
            else request.features
        )
        uses = [item.text for item in analysis.use_cases] if analysis else []
        tastes = [item.text for item in analysis.taste_notes] if analysis else []
        visible = [item.text for item in analysis.visible_features] if analysis else []
        first = points[0]
        second = points[1] if len(points) > 1 else first
        use = uses[0] if uses else f"{request.product_name}이 필요할 때"
        sensory = tastes[0] if tastes else (visible[0] if visible else second)
        is_food = any(
            word in request.category
            for word in ("농수산물", "식품", "음료", "간식", "건강식품")
        )
        everyday_phrase = "맛있게 즐기기 좋은" if is_food else "일상에서 자주 쓰기 좋은"

        def as_point(text: str) -> str:
            if text.endswith("함"):
                return f"{text[:-1]}한 점"
            if text.endswith("임"):
                return f"{text[:-1]}인 점"
            return text

        def as_use_case(text: str) -> str:
            cleaned = text.strip().rstrip(".?!")
            verb_endings = {
                "사용하기": "사용할 때",
                "준비하기": "준비할 때",
                "활용하기": "활용할 때",
                "보관하기": "보관할 때",
                "세척하기": "세척할 때",
                "즐기기": "즐길 때",
                "마시기": "마실 때",
                "챙기기": "챙길 때",
                "먹기": "먹을 때",
                "쓰기": "쓸 때",
                "하기": "할 때",
            }
            for ending, replacement in verb_endings.items():
                if cleaned.endswith(ending):
                    cleaned = f"{cleaned[:-len(ending)]}{replacement}"
                    break
            if cleaned.endswith("때"):
                return f"{cleaned}, 잘 어울려요."
            return f"{cleaned}에 잘 어울려요."

        first_point = as_point(first)

        if request.tone == "shopping":
            hook = f"{request.product_name}, 요즘 왜 눈에 띄는지 볼까요?"
            opening = f"이거 한번 보세요. {first}이라는 점부터 눈에 띄어요."
        elif request.tone == "calm_review":
            hook = f"{request.product_name}, 사진에서 보이는 포인트를 살펴봤어요."
            opening = f"가장 먼저 보이는 특징은 {first}이에요."
        else:
            hook = f"{request.product_name}, 이건 좀 눈에 들어오죠?"
            opening = f"첫 느낌부터 괜찮아 보여요. {first_point}이 딱 눈에 띄어요."

        def caption(text: str) -> str:
            return text.strip()[:22]

        return ScriptDraft(
            hook=hook,
            selection_criteria=points[:2],
            pros=points[:2],
            cons=["구성과 세부 정보는 상품 페이지에서 한번 더 보세요."],
            cta="궁금한 구성과 가격은 설명란 링크에서 볼 수 있어요.",
            scenes=[
                SceneDraft(order=1, narration=opening, on_screen_text=caption(f"{request.product_name}, 이건 좀 눈에 들어오죠?"), duration_seconds=part),
                SceneDraft(order=2, narration=f"특히 {first_point}이 매력적이에요. {everyday_phrase} 포인트죠.", on_screen_text=caption(first), duration_seconds=part),
                SceneDraft(order=3, narration=as_use_case(use), on_screen_text=caption(use), duration_seconds=part),
                SceneDraft(order=4, narration=f"사진만 봐도 {sensory}. 이 포인트가 잘 살아있어요.", on_screen_text=caption(sensory), duration_seconds=part),
                SceneDraft(order=5, narration="궁금한 구성과 가격은 설명란 링크에서 볼 수 있어요.", on_screen_text="궁금하면 아래 링크에서", duration_seconds=part),
            ],
            disclosure=AFFILIATE_DISCLOSURE,
        )


class FakeSpeechProvider:
    def __init__(self, seconds: float = 3.0):
        self.seconds = seconds

    async def generate(
        self, text: str, output_path: Path, voice: str, instructions: str
    ) -> SpeechResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rate = 24_000
        with wave.open(str(output_path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(b"\0\0" * int(rate * self.seconds))
        return SpeechResult(
            path=output_path,
            duration_seconds=self.seconds,
            provider="fake",
            model="silence",
        )
