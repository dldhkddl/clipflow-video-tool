import base64
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import imageio_ffmpeg
from openai import OpenAI

from .db import Database
from .models import AnalysisItem, Asset, ProductAnalysis, Project, utc_now
from .providers.gemini import GeminiClient, JsonPost


ANALYSIS_PROMPT = """
사용자가 홍보 영상에 쓸 상품 이미지를 분석한다.
이미지에서 직접 확인한 외형과 포장 글자는 origin을 visual로 표시한다.
맛, 향, 식감, 사용 상황, 홍보 아이디어는 자연스러운 한국어 광고 표현으로 작성하고 origin을 ai_promotion으로 표시한다.
사진으로 확인할 수 없는 제조사, 원산지, 성분, 가격을 단정하지 말고 질병 치료나 예방 효능을 작성하지 말라.
응답은 제공된 ProductAnalysis 구조를 정확히 따른다.
""".strip()


class ProductAnalysisService:
    def __init__(
        self,
        database: Database,
        *,
        offline: bool,
        api_key: str | None = None,
        model: str = "gpt-5.6-terra",
        gemini_api_key: str | None = None,
        gemini_model: str = "gemini-3.6-flash",
        gemini_json_post: JsonPost | None = None,
    ):
        self.database = database
        self.offline = offline
        self.model = model
        self.client = OpenAI(api_key=api_key) if not offline and api_key else None
        self.gemini_client = (
            GeminiClient(gemini_api_key, gemini_model, json_post=gemini_json_post)
            if not offline and gemini_api_key
            else None
        )

    def generate(self, project: Project, assets: list[Asset]) -> ProductAnalysis:
        visuals = [
            asset
            for asset in assets
            if asset.media_type in {"image", "video"} and asset.rights_approved
        ]
        if not visuals:
            raise ValueError("사용 권리를 확인한 이미지나 영상을 먼저 선택하세요.")
        if self.offline:
            return self._demo_analysis(project, visuals)
        if self.gemini_client is not None:
            return self._gemini_analysis(project, visuals)
        if self.client is None:
            raise ValueError("GEMINI_API_KEY 또는 OPENAI_API_KEY를 .env에 설정하세요.")
        return self._vision_analysis(project, visuals)

    def save(self, project_id: str, analysis: ProductAnalysis) -> ProductAnalysis:
        with self.database.connection() as conn:
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM product_analyses WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO product_analyses (id, project_id, version, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid4()), project_id, version, analysis.model_dump_json(), utc_now()),
            )
        return analysis

    def latest(self, project_id: str) -> ProductAnalysis | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT content_json FROM product_analyses WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return ProductAnalysis.model_validate_json(row[0]) if row else None

    @staticmethod
    def _demo_analysis(project: Project, assets: list[Asset]) -> ProductAnalysis:
        promotion = [AnalysisItem(text=feature, origin="user") for feature in project.features]
        return ProductAnalysis(
            product_name=project.product_name,
            category=project.category,
            visible_features=[
                AnalysisItem(text=f"{project.product_name}이 돋보이는 상품 이미지", origin="visual")
            ],
            package_text=[],
            taste_notes=[
                AnalysisItem(text="보기만 해도 맛과 향이 기대되는 비주얼", origin="ai_promotion")
            ],
            use_cases=[
                AnalysisItem(text="집에서 편하게 즐길 때", origin="ai_promotion"),
                AnalysisItem(text="선물용으로 준비할 때", origin="ai_promotion"),
            ],
            promotion_points=promotion,
            visual_direction=[
                AnalysisItem(text="밝고 깨끗한 제품 광고 분위기", origin="ai_promotion")
            ],
            source_asset_ids=[asset.id for asset in assets],
            mode="demo",
            confirmed=False,
        )

    def _vision_analysis(self, project: Project, assets: list[Asset]) -> ProductAnalysis:
        content: list[dict] = [
            {
                "type": "input_text",
                "text": (
                    f"사용자 입력 상품명: {project.product_name}\n"
                    f"카테고리: {project.category}\n"
                    f"사용자 입력 특징: {', '.join(project.features)}"
                ),
            }
        ]
        with TemporaryDirectory(prefix="clipflow-analysis-") as temp_dir:
            for path, content_type in self._analysis_images(assets, Path(temp_dir)):
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{content_type};base64,{encoded}",
                    }
                )
            response = self.client.responses.parse(
                model=self.model,
                reasoning={"effort": "low"},
                input=[
                    {"role": "system", "content": ANALYSIS_PROMPT},
                    {"role": "user", "content": content},
                ],
                text_format=ProductAnalysis,
            )
        if response.output_parsed is None:
            raise RuntimeError("상품 이미지를 분석하지 못했습니다.")
        return response.output_parsed.model_copy(
            update={
                "product_name": project.product_name,
                "category": project.category,
                "source_asset_ids": [asset.id for asset in assets],
                "mode": "vision",
                "confirmed": False,
            }
        )

    def _gemini_analysis(self, project: Project, assets: list[Asset]) -> ProductAnalysis:
        prompt = (
            f"사용자 입력 상품명: {project.product_name}\n"
            f"카테고리: {project.category}\n"
            f"사용자 입력 특징: {', '.join(project.features)}"
        )
        with TemporaryDirectory(prefix="clipflow-analysis-") as temp_dir:
            image_parts = []
            for path, content_type in self._analysis_images(assets, Path(temp_dir)):
                image_parts.append(
                    {
                        "inline_data": {
                            "mime_type": content_type,
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    }
                )
            value = self.gemini_client.generate_json(
                prompt=prompt,
                schema=ProductAnalysis.model_json_schema(),
                system_prompt=ANALYSIS_PROMPT,
                image_parts=image_parts,
            )
        value.update(
            {
                "product_name": project.product_name,
                "category": project.category,
                "source_asset_ids": [asset.id for asset in assets],
                "mode": "vision",
                "confirmed": False,
            }
        )
        return ProductAnalysis.model_validate(value)

    @staticmethod
    def _analysis_images(assets: list[Asset], temp_dir: Path) -> list[tuple[Path, str]]:
        images: list[tuple[Path, str]] = []
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        for asset in assets:
            if asset.media_type == "image":
                images.append((asset.path, asset.content_type))
                continue
            for index, second in enumerate((0, 2, 4)):
                frame = temp_dir / f"{asset.id}-{index}.jpg"
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-ss",
                        str(second),
                        "-i",
                        str(asset.path),
                        "-frames:v",
                        "1",
                        str(frame),
                    ],
                    shell=False,
                    timeout=60,
                    check=False,
                    capture_output=True,
                )
                if frame.is_file() and frame.stat().st_size:
                    images.append((frame, "image/jpeg"))
        if not images:
            raise ValueError("분석할 수 있는 이미지 장면을 찾지 못했습니다.")
        return images[:8]
