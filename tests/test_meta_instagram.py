from clipflow.meta_instagram import MetaInstagramClient


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, data, headers):
        self.calls.append((method, url, data, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(transport, user_id="17841400000000000"):
    return MetaInstagramClient(
        access_token="secret-access-token",
        user_id=user_id,
        transport=transport,
    )


def test_status_confirms_the_configured_account_without_exposing_token():
    transport = FakeTransport(
        {
            "id": "17841400000000000",
            "username": "fruit_born",
            "account_type": "MEDIA_CREATOR",
        }
    )

    result = make_client(transport).status()

    assert result == {
        "configured": True,
        "connected": True,
        "id": "17841400000000000",
        "username": "fruit_born",
        "account_type": "MEDIA_CREATOR",
    }
    method, url, data, headers = transport.calls[0]
    assert method == "GET"
    assert url.endswith("/me?fields=id%2Cusername%2Caccount_type")
    assert data is None
    assert headers["Authorization"] == "Bearer secret-access-token"
    assert "secret-access-token" not in str(result)


def test_status_rejects_a_different_instagram_account():
    transport = FakeTransport(
        {"id": "different", "username": "other", "account_type": "BUSINESS"}
    )

    result = make_client(transport).status()

    assert result["connected"] is False
    assert result["error"] == "설정된 Instagram 계정 ID와 토큰 계정이 다릅니다."


def test_create_check_and_publish_reel_use_the_meta_endpoints():
    transport = FakeTransport(
        {"id": "container-1"},
        {"id": "container-1", "status_code": "FINISHED", "status": "Finished"},
        {"id": "media-1"},
    )
    client = make_client(transport)

    assert client.create_reel("https://public.example/video.mp4", "광고 포함") == "container-1"
    assert client.container_status("container-1")["status_code"] == "FINISHED"
    assert client.publish("container-1") == "media-1"

    create = transport.calls[0]
    assert create[0] == "POST"
    assert create[1].endswith("/17841400000000000/media")
    assert create[2] == {
        "media_type": "REELS",
        "video_url": "https://public.example/video.mp4",
        "caption": "광고 포함",
        "share_to_feed": "true",
    }
    assert transport.calls[1][1].endswith(
        "/container-1?fields=id%2Cstatus_code%2Cstatus"
    )
    assert transport.calls[2][2] == {"creation_id": "container-1"}


def test_transport_failure_is_sanitized():
    transport = FakeTransport(RuntimeError("secret-access-token leaked"))

    result = make_client(transport).status()

    assert result["connected"] is False
    assert result["error"] == "Instagram 연결을 확인하지 못했습니다."
    assert "secret-access-token" not in str(result)
