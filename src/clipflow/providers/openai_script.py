from openai import AsyncOpenAI

from ..models import AFFILIATE_DISCLOSURE, ScriptDraft, ScriptRequest


SYSTEM_PROMPT = f"""당신은 한국어 쇼핑 숏폼과 릴스 홍보 대본 작가입니다.
사용자가 확정한 analysis를 핵심 근거로 쓰고, 사용하지 않은 개인 경험은 만들지 마십시오.
맛·향·식감은 analysis의 홍보 표현을 자연스럽게 쓸 수 있지만 질병 치료·예방, 확인되지 않은 수치, 가격·재고를 단정하지 마십시오.
자막은 한 장면당 8~18자를 목표로 짧게 쓰고 22자를 넘지 마십시오.
음성과 자막을 똑같이 복사하지 말고 `선택 기준`, `확인하세요`, `사용 환경의 한계`와 같은 딱딱한 설명서식 표현을 피하십시오.
tone이 friend면 친구가 추천하듯, shopping이면 밝고 신나게, calm_review면 차분한 리뷰처럼 작성하십시오.
4~7개 장면으로 구성하고 마지막 장면에만 링크 안내를 넣으십시오.
경제적 이해관계 고지는 정확히 다음 문장을 사용하십시오: {AFFILIATE_DISCLOSURE}
"""


class OpenAIScriptProvider:
    def __init__(self, api_key: str, model: str = "gpt-5.6-terra"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate(self, request: ScriptRequest) -> ScriptDraft:
        response = await self.client.responses.parse(
            model=self.model,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": request.model_dump_json()},
            ],
            text_format=ScriptDraft,
        )
        if response.output_parsed is None:
            raise RuntimeError("구조화된 대본을 생성하지 못했습니다.")
        return response.output_parsed
