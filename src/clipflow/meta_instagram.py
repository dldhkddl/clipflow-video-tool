from collections.abc import Callable
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


Transport = Callable[[str, str, dict[str, str] | None, dict[str, str]], dict[str, Any]]


class MetaInstagramError(RuntimeError):
    """사용자에게 비밀값을 노출하지 않는 Meta API 오류."""


def _default_transport(
    method: str,
    url: str,
    data: dict[str, str] | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    body = urlencode(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise MetaInstagramError("Instagram API 요청을 처리하지 못했습니다.") from exc
    if not isinstance(payload, dict):
        raise MetaInstagramError("Instagram API 응답 형식이 올바르지 않습니다.")
    if payload.get("error"):
        raise MetaInstagramError("Instagram API가 요청을 거부했습니다.")
    return payload


class MetaInstagramClient:
    api_base = "https://graph.instagram.com"

    def __init__(
        self,
        access_token: str | None,
        user_id: str | None,
        transport: Transport | None = None,
    ):
        self._access_token = (access_token or "").strip()
        self.user_id = (user_id or "").strip()
        self._transport = transport or _default_transport

    @property
    def configured(self) -> bool:
        return bool(self._access_token and self.user_id)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise MetaInstagramError("Instagram 연결 정보가 설정되지 않았습니다.")
        url = f"{self.api_base}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        try:
            result = self._transport(method, url, data, self._headers())
        except MetaInstagramError:
            raise
        except Exception as exc:
            raise MetaInstagramError("Instagram API 요청을 처리하지 못했습니다.") from exc
        if not isinstance(result, dict) or result.get("error"):
            raise MetaInstagramError("Instagram API가 요청을 거부했습니다.")
        return result

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "connected": False}
        try:
            payload = self._request(
                "GET",
                "/me",
                query={"fields": "id,username,account_type"},
            )
        except MetaInstagramError:
            return {
                "configured": True,
                "connected": False,
                "error": "Instagram 연결을 확인하지 못했습니다.",
            }
        result = {
            "configured": True,
            "connected": payload.get("id") == self.user_id,
            "id": payload.get("id"),
            "username": payload.get("username"),
            "account_type": payload.get("account_type"),
        }
        if not result["connected"]:
            result["error"] = "설정된 Instagram 계정 ID와 토큰 계정이 다릅니다."
        return result

    def create_reel(self, video_url: str, caption: str) -> str:
        payload = self._request(
            "POST",
            f"/{self.user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
            },
        )
        container_id = payload.get("id")
        if not container_id:
            raise MetaInstagramError("Instagram 게시 준비 ID를 받지 못했습니다.")
        return str(container_id)

    def container_status(self, container_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/{container_id}",
            query={"fields": "id,status_code,status"},
        )

    def publish(self, container_id: str) -> str:
        payload = self._request(
            "POST",
            f"/{self.user_id}/media_publish",
            data={"creation_id": container_id},
        )
        media_id = payload.get("id")
        if not media_id:
            raise MetaInstagramError("Instagram 게시물 ID를 받지 못했습니다.")
        return str(media_id)
