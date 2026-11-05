"""External-service failures must reach the browser as readable errors, with CORS headers."""
import httpx
import pytest
from fastapi.testclient import TestClient

from core.media import anilist
from core.media.provider_transport import ProviderError

ORIGIN = {"Origin": "http://localhost:5179"}


def test_connection_failure_is_a_502_that_passes_cors(monkeypatch):
    async def unreachable(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(anilist, "_query", unreachable)
    from main import app

    r = TestClient(app).get("/api/discover/anime/browse", headers=ORIGIN)

    assert r.status_code == 502
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5179"
    assert "internet connection" in r.json()["detail"]


def test_graphql_message_is_reported_as_400_not_500(monkeypatch):
    async def bad_query(*args, **kwargs):
        raise ValueError('AniList error: Cannot query field "voiceActors" on type "Character".')

    monkeypatch.setattr(anilist, "_query", bad_query)
    from main import app

    r = TestClient(app).get("/api/discover/anime/16498", headers=ORIGIN)

    assert r.status_code == 400
    assert "voiceActors" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5179"


async def test_anilist_upstream_errors_are_safe_and_typed(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"errors": [{"message": 'Cannot query field "voiceActors"'}], "data": None})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(anilist.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    with pytest.raises(ProviderError, match="Provider rejected") as error:
        await anilist._query("query { Media { id } }")
    assert error.value.status == 400 and error.value.code == 'upstream'
