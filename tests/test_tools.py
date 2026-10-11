"""Tool behaviour against a mocked Hava API, modelled on real responses."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from hava_mcp.server import (
    hava_auth_status,
    hava_export_diagram,
    hava_find_diagrams,
    hava_get_diagram,
    hava_get_job,
    hava_list_diagrams,
    hava_list_sources,
    mcp,
)

BASE_URL = "https://api.hava.test"

VIEW_INFRA = "c621be8c-1ba8-486a-8982-c926db37b32e"
VIEW_LIST = "9c3a21a4-3f91-4085-8d04-d86a97b3f486"
DIAGRAM_ID = "01a106a3-6886-7a41-9680-834bd3ca44b5"
JOB_ID = "01a1168e-acf6-7c12-a777-79bfe9c502fd"
CDN_URL = "https://cdn.cloudfront.test/hava-export.png"

ENVIRONMENT = {
    "id": DIAGRAM_ID,
    "name": "hibob-vpc-eu-west-1",
    "state": "active",
    "environment_type": "imported",
    "query": "vpc:vpc-026b2d83444dfa0d9",
    "current_revision": {"id": "rev-1"},
    "sources": [
        {
            "id": "src-1",
            "display_name": "Hibob - 581316237707",
            "type": "Sources::AWS::CrossAccountRole",
            "provider_account_id": "581316237707",
        }
    ],
    "views": [
        {"id": VIEW_INFRA, "type": "Views::Infrastructure", "regions": ["eu-west-1"], "empty": False},
        {"id": VIEW_LIST, "type": "Views::List", "regions": ["eu-west-1"]},
    ],
    "facet": {
        "id": "facet-1",
        "resources": [
            {"id": f"r-{i}", "name": f"instance-{i}", "type": "Resources::AWS::EC2::Instance",
             "region": "eu-west-1", "provider_id": f"i-{i}", "price": 12.3}
            for i in range(5)
        ],
    },
}

SUMMARY = {k: ENVIRONMENT[k] for k in ("id", "name", "state", "environment_type", "query", "sources", "views")}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

#: Every tool the server is expected to expose, by API area.
EXPECTED_TOOLS = {
    # auth
    "hava_auth_status",
    # diagrams (environments)
    "hava_list_diagrams", "hava_find_diagrams", "hava_get_diagram",
    "hava_list_diagram_versions", "hava_create_diagram", "hava_update_diagram",
    "hava_delete_diagram",
    # export + jobs
    "hava_export_diagram", "hava_get_job",
    # resources
    "hava_list_resources", "hava_export_resources",
    # notes + shares
    "hava_list_diagram_notes", "hava_create_diagram_note", "hava_delete_diagram_note",
    "hava_list_diagram_shares", "hava_get_diagram_share", "hava_create_diagram_share",
    "hava_update_diagram_share",
    # reports
    "hava_list_reports", "hava_get_report", "hava_export_report",
    # sources
    "hava_list_sources", "hava_get_source", "hava_sync_source",
    "hava_rename_source", "hava_delete_source", "hava_create_source",
    "hava_update_source_credentials",
    # org: teams
    "hava_list_teams", "hava_get_team", "hava_create_team", "hava_update_team",
    "hava_delete_team", "hava_add_team_member", "hava_invite_team_member",
    "hava_remove_team_member",
    # org: projects
    "hava_list_projects", "hava_get_project", "hava_create_project",
    "hava_update_project", "hava_delete_project", "hava_add_project_member",
    "hava_remove_project_member",
    # org: api tokens
    "hava_list_api_tokens", "hava_delete_api_token", "hava_create_api_token",
}


async def test_every_tool_is_registered_with_a_description_and_annotations():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS, (
        f"missing={sorted(EXPECTED_TOOLS - names)} unexpected={sorted(names - EXPECTED_TOOLS)}"
    )
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.inputSchema["type"] == "object"


async def test_read_only_tools_are_marked_read_only():
    by_name = {t.name: t for t in await mcp.list_tools()}
    assert by_name["hava_list_diagrams"].annotations.readOnlyHint is True
    assert by_name["hava_export_diagram"].annotations.readOnlyHint is False
    assert by_name["hava_delete_diagram"].annotations.destructiveHint is True


# --------------------------------------------------------------------------- #
# Listing and finding
# --------------------------------------------------------------------------- #

@respx.mock
async def test_list_diagrams_exposes_view_ids_and_paging(hava_client):
    respx.get(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(
            200, json={"total_size": 118, "next_page_token": "tok-2", "results": [SUMMARY]}
        )
    )
    text = await hava_list_diagrams(page_size=25)
    assert "hibob-vpc-eu-west-1" in text
    assert VIEW_INFRA in text          # the ID an export needs
    assert "of 118 total" in text
    assert "tok-2" in text             # how to continue


@respx.mock
async def test_list_diagrams_json_format_is_structured(hava_client):
    respx.get(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"total_size": 1, "results": [SUMMARY]})
    )
    data = json.loads(await hava_list_diagrams(response_format="json"))
    assert data["total"] == 1 and data["count"] == 1
    diagram = data["diagrams"][0]
    assert diagram["diagram_id"] == DIAGRAM_ID
    assert {v["view"] for v in diagram["views"]} == {"infrastructure", "list"}


@respx.mock
async def test_list_diagrams_defaults_to_a_stable_sort(hava_client):
    """Token paging carries the sort, so page 1 must request one."""
    route = respx.get(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await hava_list_diagrams()
    params = route.calls.last.request.url.params
    assert params["sort"] == "name" and params["sort_dir"] == "asc"


@respx.mock
async def test_page_token_replaces_the_other_paging_params(hava_client):
    route = respx.get(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await hava_list_diagrams(page_token="tok-2", page_size=50, sort="name")
    params = route.calls.last.request.url.params
    assert params["token"] == "tok-2"
    assert "page_size" not in params and "sort" not in params


async def test_find_diagrams_requires_a_filter(hava_client):
    text = await hava_find_diagrams()
    assert "hava_list_diagrams" in text and text.startswith("Error:")


@respx.mock
async def test_find_diagrams_by_name_walks_pages_and_matches_case_insensitively(hava_client):
    other = {**SUMMARY, "id": "other-id", "name": "unrelated-vpc"}
    respx.get(f"{BASE_URL}/environments").mock(
        side_effect=[
            httpx.Response(200, json={"next_page_token": "p2", "results": [other]}),
            httpx.Response(200, json={"results": [SUMMARY]}),
        ]
    )
    text = await hava_find_diagrams(name_contains="HIBOB-VPC")
    assert "hibob-vpc-eu-west-1" in text
    assert "unrelated-vpc" not in text
    assert "Scanned 2 diagram(s)" in text


@respx.mock
async def test_find_diagrams_by_resource_query_returns_deep_links(hava_client):
    respx.get(f"{BASE_URL}/search/environments").mock(
        return_value=httpx.Response(
            202,
            json={"total_size": 1, "results": [{
                "id": DIAGRAM_ID, "name": "hibob-vpc-eu-west-1",
                "url": "https://app.hava.io/env/1?selected_id=i-1",
                "image": "https://cdn/thumb.png",
            }]},
        )
    )
    text = await hava_find_diagrams(resource_query="vpc:vpc-026b2d83444dfa0d9")
    assert "selected_id=i-1" in text


@respx.mock
async def test_search_failure_suggests_the_name_fallback(hava_client):
    """The live endpoint 500s on some queries; say what to do about it."""
    respx.get(f"{BASE_URL}/search/environments").mock(return_value=httpx.Response(500))
    text = await hava_find_diagrams(resource_query="vpc:vpc-1")
    assert "name_contains" in text and "field:value" in text


@respx.mock
async def test_list_sources_handles_the_object_shaped_response(hava_client):
    """GET /sources answers {total_size, results}, not a bare array."""
    respx.get(f"{BASE_URL}/sources").mock(
        return_value=httpx.Response(200, json={"total_size": 1, "results": [
            {"id": "src-1", "display_name": "Hibob - 581316237707",
             "type": "Sources::AWS::CrossAccountRole",
             "provider_account_id": "581316237707", "state": "active"}
        ]})
    )
    data = json.loads(await hava_list_sources(response_format="json"))
    assert data["count"] == 1
    assert data["sources"][0]["name"] == "Hibob - 581316237707"


# --------------------------------------------------------------------------- #
# Diagram detail
# --------------------------------------------------------------------------- #

@respx.mock
async def test_get_diagram_omits_resources_by_default(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    data = json.loads(await hava_get_diagram(diagram_id=DIAGRAM_ID, response_format="json"))
    assert data["resources"] is None
    assert data["diagram"]["resource_count"] == 5     # counted, not inlined
    assert data["diagram"]["current_revision_id"] == "rev-1"


@respx.mock
async def test_get_diagram_truncates_resources_and_says_so(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    text = await hava_get_diagram(diagram_id=DIAGRAM_ID, include_resources=True, resource_limit=2)
    assert "Showing 2 of 5 resources" in text


@respx.mock
async def test_get_diagram_passes_the_revision_through(hava_client):
    route = respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    await hava_get_diagram(diagram_id=DIAGRAM_ID, revision_id="rev-9")
    assert route.calls.last.request.url.params["revision_id"] == "rev-9"


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _mock_successful_export():
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    export = respx.post(f"{BASE_URL}/views/{VIEW_INFRA}/export").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB_ID}"})
    )
    respx.get(f"{BASE_URL}/jobs/{JOB_ID}").mock(
        return_value=httpx.Response(303, headers={"location": CDN_URL})
    )
    respx.get(CDN_URL).mock(return_value=httpx.Response(200, content=b"PNGDATA"))
    return export


@respx.mock
async def test_export_resolves_the_view_polls_the_job_and_returns_the_link(hava_client):
    _mock_successful_export()
    data = json.loads(await hava_export_diagram(
        diagram_id=DIAGRAM_ID, view="infrastructure", export_format="png",
        response_format="json",
    ))
    assert data["state"] == "complete"
    assert data["view_id"] == VIEW_INFRA
    assert data["job_id"] == JOB_ID
    assert data["download_url"] == CDN_URL
    assert data["saved_to"] is None      # no save_to given


@respx.mock
async def test_export_only_sends_the_options_that_were_set(hava_client):
    export = _mock_successful_export()
    await hava_export_diagram(
        diagram_id=DIAGRAM_ID, export_format="png", labels=True, isometric=False,
    )
    body = json.loads(export.calls.last.request.content)
    assert body == {"export_format": "png", "labels": True, "isometric": False}


@respx.mock
async def test_export_saves_into_a_directory_with_a_derived_filename(hava_client, tmp_path):
    _mock_successful_export()
    data = json.loads(await hava_export_diagram(
        diagram_id=DIAGRAM_ID, export_format="png", save_to=str(tmp_path),
        response_format="json",
    ))
    saved = tmp_path / "hibob-vpc-eu-west-1-infrastructure.png"
    assert data["saved_to"] == str(saved)
    assert data["bytes"] == 7
    assert saved.read_bytes() == b"PNGDATA"


@respx.mock
async def test_export_creates_missing_parent_directories(hava_client, tmp_path):
    _mock_successful_export()
    target = tmp_path / "nested" / "deeper" / "diagram.png"
    await hava_export_diagram(
        diagram_id=DIAGRAM_ID, export_format="png", save_to=str(target)
    )
    assert target.read_bytes() == b"PNGDATA"


@respx.mock
async def test_export_returns_the_job_id_when_the_wait_budget_runs_out(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    respx.post(f"{BASE_URL}/views/{VIEW_INFRA}/export").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB_ID}"})
    )
    respx.get(f"{BASE_URL}/jobs/{JOB_ID}").mock(
        return_value=httpx.Response(200, json={"id": JOB_ID, "state": "active"})
    )
    data = json.loads(await hava_export_diagram(
        diagram_id=DIAGRAM_ID, export_format="png", wait_seconds=0, response_format="json",
    ))
    assert data["state"] == "active"
    assert data["job_id"] == JOB_ID
    assert "hava_get_job" in data["note"]


@respx.mock
async def test_export_reports_a_failed_job(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    respx.post(f"{BASE_URL}/views/{VIEW_INFRA}/export").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB_ID}"})
    )
    respx.get(f"{BASE_URL}/jobs/{JOB_ID}").mock(
        return_value=httpx.Response(200, json={"id": JOB_ID, "state": "failed"})
    )
    text = await hava_export_diagram(diagram_id=DIAGRAM_ID, export_format="png")
    assert text.startswith("Error:") and "failed" in text


@respx.mock
@pytest.mark.parametrize(
    "view,fmt,usable",
    [("list", "png", "csv, json"), ("list", "pdf", "csv, json")],
)
async def test_unsupported_format_and_view_pairs_fail_before_calling_hava(
    hava_client, view, fmt, usable
):
    env_route = respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    export_route = respx.post(f"{BASE_URL}/views/{VIEW_LIST}/export")
    text = await hava_export_diagram(diagram_id=DIAGRAM_ID, view=view, export_format=fmt)
    assert text.startswith("Error:") and usable in text
    assert env_route.called
    assert not export_route.called, "should not waste an API call on a known-bad pair"


@respx.mock
async def test_export_of_a_missing_view_lists_what_exists(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}").mock(
        return_value=httpx.Response(200, json=ENVIRONMENT)
    )
    text = await hava_export_diagram(
        diagram_id=DIAGRAM_ID, view="security", export_format="png"
    )
    assert "no 'security' view" in text
    assert "infrastructure" in text and "list" in text


async def test_export_requires_a_diagram_or_view_id(hava_client):
    text = await hava_export_diagram(export_format="png")
    assert "diagram_id" in text and "view_id" in text


@respx.mock
async def test_export_accepts_a_view_id_directly_without_a_lookup(hava_client):
    env_route = respx.get(f"{BASE_URL}/environments/{DIAGRAM_ID}")
    respx.post(f"{BASE_URL}/views/{VIEW_INFRA}/export").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB_ID}"})
    )
    respx.get(f"{BASE_URL}/jobs/{JOB_ID}").mock(
        return_value=httpx.Response(303, headers={"location": CDN_URL})
    )
    data = json.loads(await hava_export_diagram(
        view_id=VIEW_INFRA, export_format="png", response_format="json"
    ))
    assert data["state"] == "complete"
    assert not env_route.called


@respx.mock
async def test_get_job_saves_the_artifact_with_the_right_extension(hava_client, tmp_path):
    respx.get(f"{BASE_URL}/jobs/{JOB_ID}").mock(
        return_value=httpx.Response(303, headers={"location": f"{CDN_URL}?Signature=abc"})
    )
    respx.get(CDN_URL).mock(return_value=httpx.Response(200, content=b"PNGDATA"))
    data = json.loads(await hava_get_job(
        job_id=JOB_ID, save_to=str(tmp_path), response_format="json"
    ))
    assert data["saved_to"].endswith(".png")


# --------------------------------------------------------------------------- #
# Auth tools
# --------------------------------------------------------------------------- #

@respx.mock
async def test_auth_status_reports_the_credential_origin(hava_client):
    respx.get(f"{BASE_URL}/sources").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "s1"}, {"id": "s2"}]})
    )
    text = await hava_auth_status()
    assert "2 cloud source(s)" in text
    assert "test-token" not in text, "the raw token must never be echoed"


@respx.mock
async def test_auth_status_explains_how_to_configure_a_missing_token():
    """With nothing configured, give the one supported command and nothing else."""
    text = await hava_auth_status()
    assert "no API token configured" in text
    assert "claude plugin configure cloudzone-hava@synced" in text
    assert "HAVA_API_TOKEN" not in text


@respx.mock
async def test_auth_status_reports_a_rejected_token(hava_client):
    respx.get(f"{BASE_URL}/sources").mock(return_value=httpx.Response(401))
    text = await hava_auth_status()
    assert "FAILED" in text




