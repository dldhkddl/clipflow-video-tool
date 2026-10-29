import asyncio
import json
from collections.abc import Callable
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models import AFFILIATE_DISCLOSURE, ScriptDraft, ScriptRequest


GEMINI_SCRIPT_PROMPT = f"""
한국어 쇼츠·릴스 상품 홍보 대본을 작성한다.
사용자가 확정한 상품 정보와 이미지 분석만 사실 근거로 사용한다.
맛·향·식감은 광고 표현으로 자연스럽게 쓸 수 있지만 실제 구매나 사용 경험을 지어내지 않는다.
확인하지 않은 원산지, 성분, 가격, 재고, 치료·예방 효능을 단정하지 않는다.
말투는 친구가 실제로 짧게 추천하듯 자연스럽게 쓰고 딱딱한 보고서 표현을 피한다.
화면 자막은 한 장면에 한 문장, 22자 이내를 목표로 한다.
4~7개 장면의 시간 합계는 요청한 영상 길이와 같아야 한다.
마지막 장면에만 링크 확인 안내를 넣는다.
광고 고지는 다음 문장을 그대로 사용한다: {AFFILIATE_DISCLOSURE}
""".strip()


class GeminiError(ValueError):
    pass


JsonPost = Callable[[str, dict[str, str], dict], dict]
FileUpload = Callable[[Path, str], dict]
FileGet = Callable[[str], dict]
FileDelete = Callable[[str], None]


class GeminiClient:
    API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.6-flash",
        *,
        json_post: JsonPost | None = None,
        file_upload: FileUpload | None = None,
        file_get: FileGet | None = None,
        file_delete: FileDelete | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.api_key = api_key.strip()
        self.model = model.removeprefix("models/").strip()
        self.json_post = json_post or self._default_json_post
        self.file_upload = file_upload or self._default_file_upload
        self.file_get = file_get or self._default_file_get
        self.file_delete = file_delete or self._default_file_delete
        self.sleep = sleep or time.sleep

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict,
        system_prompt: str,
        image_parts: list[dict] | None = None,
    ) -> dict:
        parts = [{"text": prompt}, *(image_parts or [])]
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0.8,
            },
        }
        response = self.json_post(
            f"{self.API_ROOT}/{self.model}:generateContent",
            {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            payload,
        )
        return self._response_json(response)

    def generate_json_from_file(
        self,
        *,
        prompt: str,
        schema: dict,
        system_prompt: str,
        file_path: Path,
        content_type: str,
    ) -> dict:
        uploaded = self.file_upload(file_path, content_type)
        name = str(uploaded.get("name") or "")
        try:
            for _attempt in range(120):
                state = str(uploaded.get("state") or "").upper()
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    raise GeminiError("Gemini가 영상 파일을 처리하지 못했습니다.")
                if not name:
                    raise GeminiError("Gemini 영상 파일 이름을 확인할 수 없습니다.")
                self.sleep(5)
                uploaded = self.file_get(name)
            else:
                raise GeminiError("Gemini 영상 분석 준비 시간이 초과되었습니다.")

            file_uri = str(uploaded.get("uri") or "")
            if not file_uri:
                raise GeminiError("Gemini 영상 파일 주소를 확인할 수 없습니다.")
            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "file_data": {
                                "mime_type": content_type,
                                "file_uri": file_uri,
                            }
                        },
                    ],
                }],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                    "temperature": 0.8,
                },
            }
            response = self.json_post(
                f"{self.API_ROOT}/{self.model}:generateContent",
                {
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                payload,
            )
            return self._response_json(response)
        finally:
            if name:
                try:
                    self.file_delete(name)
                except (GeminiError, OSError):
                    pass

    @staticmethod
    def _response_json(response: dict) -> dict:
        try:
            parts = response["candidates"][0]["content"]["parts"]
            text = next(part["text"] for part in parts if part.get("text"))
            value = json.loads(text)
        except (KeyError, IndexError, StopIteration, TypeError, json.JSONDecodeError) as exc:
            raise GeminiError("Gemini가 구조화된 결과를 반환하지 못했습니다.") from exc
        if not isinstance(value, dict):
            raise GeminiError("Gemini 응답 형식이 올바르지 않습니다.")
        return value

    def _default_file_upload(self, file_path: Path, content_type: str) -> dict:
        data = file_path.read_bytes()
        start = Request(
            "https://generativelanguage.googleapis.com/upload/v1beta/files",
            data=json.dumps(
                {"file": {"display_name": file_path.name}}, ensure_ascii=False
            ).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(data)),
                "X-Goog-Upload-Header-Content-Type": content_type,
            },
            method="POST",
        )
        try:
            with urlopen(start, timeout=60) as response:
                upload_url = response.headers.get("x-goog-upload-url")
            if not upload_url:
                raise GeminiError("Gemini 영상 업로드 주소를 받지 못했습니다.")
            upload = Request(
                upload_url,
                data=data,
                headers={
                    "Content-Length": str(len(data)),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                    "Content-Type": content_type,
                },
                method="POST",
            )
            with urlopen(upload, timeout=180) as response:
                value = json.loads(response.read().decode("utf-8"))
            return value.get("file", value)
        except HTTPError as exc:
            raise GeminiError(f"Gemini 영상 업로드에 실패했습니다: HTTP {exc.code}") from exc
        except URLError as exc:
            raise GeminiError("Gemini 영상 업로드 서버에 연결할 수 없습니다.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GeminiError("Gemini 영상 업로드 응답을 해석할 수 없습니다.") from exc

    def _default_file_get(self, name: str) -> dict:
        request = Request(
            f"https://generativelanguage.googleapis.com/v1beta/{name}",
            headers={"x-goog-api-key": self.api_key},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GeminiError("Gemini 영상 처리 상태를 확인하지 못했습니다.") from exc

    def _default_file_delete(self, name: str) -> None:
        request = Request(
            f"https://generativelanguage.googleapis.com/v1beta/{name}",
            headers={"x-goog-api-key": self.api_key},
            method="DELETE",
        )
        try:
            with urlopen(request, timeout=30):
                return
        except (HTTPError, URLError) as exc:
            raise GeminiError("Gemini 임시 영상 파일을 정리하지 못했습니다.") from exc

    @staticmethod
    def _default_json_post(url: str, headers: dict[str, str], payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = f"HTTP {exc.code}"
            try:
                body = json.loads(exc.read().decode("utf-8"))
                message = body.get("error", {}).get("message") or message
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise GeminiError(f"Gemini 요청에 실패했습니다: {message}") from exc
        except URLError as exc:
            raise GeminiError("Gemini 서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.") from exc
        except TimeoutError as exc:
            raise GeminiError("Gemini 영상 분석 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.") from exc


class GeminiScriptProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.6-flash",
        *,
        json_post: JsonPost | None = None,
    ):
        self.client = GeminiClient(api_key, model, json_post=json_post)

    async def generate(self, request: ScriptRequest) -> ScriptDraft:
        value = await asyncio.to_thread(
            self.client.generate_json,
            prompt=request.model_dump_json(),
            schema=ScriptDraft.model_json_schema(),
            system_prompt=GEMINI_SCRIPT_PROMPT,
        )
        value["disclosure"] = AFFILIATE_DISCLOSURE
        return ScriptDraft.model_validate(value)
