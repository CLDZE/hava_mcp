"""Transport behaviour: error translation, redirect handling, download auth."""

from __future__ import annotations

import httpx
import pytest
import respx

from hava_mcp.client import HavaClient, job_id_from_location
from hava_mcp.errors import HavaError

BASE_URL = "https://api.hava.test"


def make_client() -> HavaClient:
    return HavaClient(token="test-token", base_url=BASE_URL)


@respx.mock
async def test_request_sends_the_bearer_token():
    route = respx.get(f"{BASE_URL}/sources").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = make_client()
    await client.get_json("/sources")
    await client.aclose()
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
async def test_none_valued_params_are_not_sent():
    route = respx.get(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = make_client()
    await client.get_json("/environments", params={"page_size": 25, "sort": None})
    await client.aclose()
    assert route.calls.last.request.url.params.get("sort") is None
    assert route.calls.last.request.url.params["page_size"] == "25"


@respx.mock
@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "HAVA_API_TOKEN"),
        (403, "Forbidden"),
        (404, "hava_list_diagrams"),
        (429, "rate limit"),
        (500, "server error"),
    ],
)
async def test_http_errors_become_actionable_messages(status, expected):
    respx.get(f"{BASE_URL}/environments").mock(return_value=httpx.Response(status))
    client = make_client()
    with pytest.raises(HavaError) as info:
        await client.get_json("/environments")
    await client.aclose()
    assert expected.lower() in str(info.value).lower()


@respx.mock
async def test_validation_errors_include_the_hava_detail():
    respx.post(f"{BASE_URL}/views/v1/export").mock(
        return_value=httpx.Response(
            422,
            json=[{"code": "invalid", "title": "Validation Error",
                   "detail": "Supported view types: Views::Infrastructure",
                   "path": "export_format"}],
        )
    )
    client = make_client()
    with pytest.raises(HavaError) as info:
        await client.request("POST", "/views/v1/export", json={"export_format": "vsdx"})
    await client.aclose()
    assert "Supported view types" in str(info.value)
    assert "export_format" in str(info.value)


@respx.mock
async def test_redirects_are_not_followed_so_the_location_survives():
    respx.get(f"{BASE_URL}/jobs/j1").mock(
        return_value=httpx.Response(303, headers={"location": "/exports/file.png"})
    )
    client = make_client()
    response = await client.request("GET", "/jobs/j1")
    await client.aclose()
    assert response.status_code == 303
    assert response.headers["location"] == "/exports/file.png"


def test_absolute_url_joins_relative_locations():
    client = make_client()
    assert client.absolute_url("/exports/a.png") == f"{BASE_URL}/exports/a.png"
    assert client.absolute_url("https://cdn.example.com/a.png") == "https://cdn.example.com/a.png"


def test_is_api_host_distinguishes_the_cdn():
    client = make_client()
    assert client.is_api_host(f"{BASE_URL}/exports/a.png")
    assert not client.is_api_host("https://cdn.cloudfront.net/a.png")


@respx.mock
async def test_download_omits_the_token_for_presigned_cdn_urls():
    """Sending the Hava token to a third-party CDN would leak it."""
    route = respx.get("https://cdn.cloudfront.net/a.png").mock(
        return_value=httpx.Response(200, content=b"PNGDATA")
    )
    client = make_client()
    content, _ = await client.download("https://cdn.cloudfront.net/a.png")
    await client.aclose()
    assert content == b"PNGDATA"
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_download_keeps_the_token_for_api_hosted_artifacts():
    route = respx.get(f"{BASE_URL}/exports/a.png").mock(
        return_value=httpx.Response(200, content=b"X")
    )
    client = make_client()
    await client.download(f"{BASE_URL}/exports/a.png")
    await client.aclose()
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
async def test_timeouts_are_reported_as_timeouts():
    respx.get(f"{BASE_URL}/environments").mock(side_effect=httpx.ConnectTimeout)
    client = make_client()
    with pytest.raises(HavaError) as info:
        await client.get_json("/environments")
    await client.aclose()
    assert "timed out" in str(info.value)


@pytest.mark.parametrize(
    "location,expected",
    [
        ("/jobs/1d2aed5b-e35e-44e1-9058-18398fe89a41", "1d2aed5b-e35e-44e1-9058-18398fe89a41"),
        ("https://api.hava.io/jobs/1d2aed5b-e35e-44e1-9058-18398fe89a41", "1d2aed5b-e35e-44e1-9058-18398fe89a41"),
        ("/jobs/opaque-id", "opaque-id"),
        ("", None),
    ],
)
def test_job_id_from_location(location, expected):
    assert job_id_from_location(location) == expected
