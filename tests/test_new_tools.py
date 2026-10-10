"""The wider API surface: resources, custom diagrams, notes, shares, sources, org."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from hava_mcp import auth, client as client_module
from hava_mcp.tools import collaboration, custom_diagrams, org, reports, resources, sources

BASE_URL = "https://api.hava.test"
ACCOUNT = "acc00000-0000-0000-0000-000000000001"
DIAGRAM = "11111111-1111-1111-1111-111111111111"
SOURCE = "22222222-2222-2222-2222-222222222222"
JOB = "33333333-3333-3333-3333-333333333333"


def jwt_for(account_id: str) -> str:
    """A token shaped like Hava's: the account ID lives in the `sub` claim."""
    body = base64.urlsafe_b64encode(json.dumps({"sub": account_id}).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@pytest.fixture
def token_client():
    """Client whose token carries ACCOUNT, so account_id can be derived."""
    instance = client_module.HavaClient(token=jwt_for(ACCOUNT), base_url=BASE_URL)
    client_module.set_client(instance)
    return instance


# --------------------------------------------------------------------------- #
# account_id derivation
# --------------------------------------------------------------------------- #

def test_account_id_is_read_from_the_token():
    """Hava has no /accounts endpoint, so the JWT `sub` claim is the only source."""
    assert auth.account_id_from_token(jwt_for(ACCOUNT)) == ACCOUNT


def test_account_id_from_a_non_jwt_is_none():
    assert auth.account_id_from_token("not-a-jwt") is None
    assert auth.account_id_from_token("") is None


@respx.mock
async def test_org_tools_default_to_the_token_account(token_client):
    route = respx.get(f"{BASE_URL}/accounts/{ACCOUNT}/teams").mock(
        return_value=httpx.Response(200, json={"total_size": 0, "results": []})
    )
    await org.hava_list_teams()
    assert route.called


@respx.mock
async def test_explicit_account_id_overrides_the_token(token_client):
    other = "acc99999-9999-9999-9999-999999999999"
    route = respx.get(f"{BASE_URL}/accounts/{other}/teams").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await org.hava_list_teams(account_id=other)
    assert route.called


async def test_missing_account_id_explains_itself(hava_client):
    """hava_client's token is not a JWT, so derivation must fail loudly."""
    text = await org.hava_list_teams()
    assert text.startswith("Error:") and "account_id" in text


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #

RESOURCES = (
    [{"id": f"i-{i}", "name": f"inst-{i}", "type": "Resources::AWS::EC2::Instance",
      "region": "eu-west-1", "provider_id": f"i-{i}", "price": 10} for i in range(6)]
    + [{"id": f"db-{i}", "name": f"db-{i}", "type": "Resources::AWS::RDS::DBInstance",
        "region": "eu-west-1"} for i in range(2)]
)


@respx.mock
async def test_list_resources_handles_a_bare_array_and_summarises(hava_client):
    """GET /resources answers with a bare JSON array, not {results: [...]}."""
    respx.get(f"{BASE_URL}/resources").mock(return_value=httpx.Response(200, json=RESOURCES))
    data = json.loads(await resources.hava_list_resources(
        query="region:eu-west-1", limit=3, response_format="json"))
    assert data["total"] == 8
    assert data["returned"] == 3           # capped by limit
    assert data["by_type"] == {"Instance": 6, "DBInstance": 2}


@respx.mock
async def test_list_resources_type_contains_filters_client_side(hava_client):
    """The API's own type: filter returns nothing, so we filter locally."""
    respx.get(f"{BASE_URL}/resources").mock(return_value=httpx.Response(200, json=RESOURCES))
    data = json.loads(await resources.hava_list_resources(
        query="region:eu-west-1", type_contains="rds", response_format="json"))
    assert data["total"] == 2
    assert all("RDS" in r["type"] for r in data["resources"])


@respx.mock
async def test_empty_resource_result_documents_the_working_query_fields(hava_client):
    respx.get(f"{BASE_URL}/resources").mock(return_value=httpx.Response(200, json=[]))
    text = await resources.hava_list_resources(query="type:ec2_instance")
    assert "does **not** work" in text and "source:" in text and "vpc:" in text


@respx.mock
async def test_export_resources_writes_a_file_and_counts_them(hava_client, tmp_path):
    payload = json.dumps(RESOURCES).encode()
    respx.get(f"{BASE_URL}/resources/export").mock(
        return_value=httpx.Response(200, content=payload)
    )
    data = json.loads(await resources.hava_export_resources(
        save_to=str(tmp_path), response_format="json"))
    assert data["resource_count"] == 8
    assert data["bytes"] == len(payload)
    assert (tmp_path / "hava-resources.json").read_bytes() == payload


@respx.mock
async def test_export_resources_without_save_to_refuses_to_inline_it(hava_client):
    respx.get(f"{BASE_URL}/resources/export").mock(
        return_value=httpx.Response(200, content=b"[]")
    )
    text = await resources.hava_export_resources()
    assert "Pass `save_to`" in text


# --------------------------------------------------------------------------- #
# Custom diagrams
# --------------------------------------------------------------------------- #

@respx.mock
async def test_create_diagram_posts_the_query(hava_client):
    route = respx.post(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "custom", "state": "active",
                                               "environment_type": "search", "views": []})
    )
    text = await custom_diagrams.hava_create_diagram(query="vpc:vpc-1", name="custom")
    assert json.loads(route.calls.last.request.content) == {"query": "vpc:vpc-1", "name": "custom"}
    assert DIAGRAM in text


@respx.mock
async def test_create_diagram_says_so_when_still_building(hava_client):
    respx.post(f"{BASE_URL}/environments").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "state": "pending", "views": []})
    )
    text = await custom_diagrams.hava_create_diagram(query="vpc:vpc-1", wait_seconds=0)
    assert "Still building" in text and "hava_get_diagram" in text


async def test_delete_diagram_requires_confirmation(hava_client):
    text = await custom_diagrams.hava_delete_diagram(diagram_id=DIAGRAM)
    assert text.startswith("Error:") and "confirm=true" in text


@respx.mock
async def test_delete_diagram_names_what_it_removed(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "scratch-diagram"})
    )
    route = respx.delete(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(204)
    )
    text = await custom_diagrams.hava_delete_diagram(diagram_id=DIAGRAM, confirm=True)
    assert route.called
    assert "scratch-diagram" in text and "cannot be undone" in text


@respx.mock
async def test_delete_diagram_does_not_call_the_api_without_confirm(hava_client):
    route = respx.delete(f"{BASE_URL}/environments/{DIAGRAM}")
    await custom_diagrams.hava_delete_diagram(diagram_id=DIAGRAM, confirm=False)
    assert not route.called


# --------------------------------------------------------------------------- #
# Notes and shares
# --------------------------------------------------------------------------- #

@respx.mock
async def test_create_note_sends_tagged_users(hava_client):
    route = respx.post(f"{BASE_URL}/environments/{DIAGRAM}/notes").mock(
        return_value=httpx.Response(200, json={"id": "note-1", "note": "hello"})
    )
    await collaboration.hava_create_diagram_note(
        diagram_id=DIAGRAM, note="hello", tagged_user_ids=["u1", "u2"])
    assert json.loads(route.calls.last.request.content) == {
        "note": "hello", "tagged_users": ["u1", "u2"]}


@respx.mock
async def test_create_note_omits_tagged_users_when_none(hava_client):
    route = respx.post(f"{BASE_URL}/environments/{DIAGRAM}/notes").mock(
        return_value=httpx.Response(200, json={"id": "note-1", "note": "hi"})
    )
    await collaboration.hava_create_diagram_note(diagram_id=DIAGRAM, note="hi")
    assert json.loads(route.calls.last.request.content) == {"note": "hi"}


async def test_delete_note_requires_confirmation(hava_client):
    text = await collaboration.hava_delete_diagram_note(diagram_id=DIAGRAM, note_id="n1")
    assert text.startswith("Error:") and "confirm=true" in text


@respx.mock
async def test_share_listing_never_echoes_the_token(hava_client):
    respx.get(f"{BASE_URL}/environments/{DIAGRAM}/shares").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "s1", "type": "Share::Full", "url": "https://app.hava.io/s/abc",
             "token": "super-secret-share-token"}]})
    )
    text = await collaboration.hava_list_diagram_shares(diagram_id=DIAGRAM)
    assert "super-secret-share-token" not in text
    assert "https://app.hava.io/s/abc" in text


@respx.mock
async def test_create_share_maps_the_friendly_type_and_warns_it_is_public(hava_client):
    route = respx.post(f"{BASE_URL}/environments/{DIAGRAM}/shares").mock(
        return_value=httpx.Response(200, json={"id": "s1", "type": "Share::PNG",
                                               "url": "https://app.hava.io/s/x"})
    )
    text = await collaboration.hava_create_diagram_share(diagram_id=DIAGRAM, share_type="png")
    assert json.loads(route.calls.last.request.content)["type"] == "Share::PNG"
    assert "public" in text.lower()


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

@respx.mock
async def test_sync_source_returns_the_job_without_waiting(hava_client):
    respx.post(f"{BASE_URL}/sources/{SOURCE}/sync").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB}"})
    )
    data = json.loads(await sources.hava_sync_source(source_id=SOURCE, response_format="json"))
    assert data["job_id"] == JOB and data["state"] == "queued"


@respx.mock
async def test_sync_source_passes_build_metadata(hava_client):
    route = respx.post(f"{BASE_URL}/sources/{SOURCE}/sync").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB}"})
    )
    await sources.hava_sync_source(source_id=SOURCE, metadata={"build_no": 10})
    assert json.loads(route.calls.last.request.content) == {"build_no": 10}


@respx.mock
async def test_rename_source_sends_only_the_name(hava_client):
    """The same endpoint can rotate credentials; we must never send any."""
    route = respx.put(f"{BASE_URL}/sources/{SOURCE}").mock(
        return_value=httpx.Response(200, json={"id": SOURCE, "display_name": "new name"})
    )
    await sources.hava_rename_source(source_id=SOURCE, name="new name")
    assert json.loads(route.calls.last.request.content) == {"name": "new name"}


async def test_delete_source_requires_confirmation(hava_client):
    text = await sources.hava_delete_source(source_id=SOURCE)
    assert text.startswith("Error:") and "confirm=true" in text


@respx.mock
async def test_delete_source_warns_about_the_diagrams_it_takes(hava_client):
    respx.get(f"{BASE_URL}/sources/{SOURCE}").mock(
        return_value=httpx.Response(200, json={"id": SOURCE, "display_name": "Prod AWS"})
    )
    respx.delete(f"{BASE_URL}/sources/{SOURCE}").mock(return_value=httpx.Response(204))
    text = await sources.hava_delete_source(source_id=SOURCE, confirm=True)
    assert "Prod AWS" in text and "diagrams" in text and "not touched" in text


# --------------------------------------------------------------------------- #
# Org
# --------------------------------------------------------------------------- #

@respx.mock
async def test_list_teams_exposes_member_user_ids(token_client):
    respx.get(f"{BASE_URL}/accounts/{ACCOUNT}/teams").mock(
        return_value=httpx.Response(200, json={"total_size": 1, "results": [
            {"id": "t1", "name": "owners", "roles": ["owner"], "members": [
                {"id": "u1", "username": "oleg", "email": "o@example.com",
                 "first_name": "Oleg", "last_name": "G"}]}]})
    )
    data = json.loads(await org.hava_list_teams(response_format="json"))
    member = data["teams"][0]["members"][0]
    assert member["user_id"] == "u1" and member["name"] == "Oleg G"


@respx.mock
async def test_add_team_member_sends_email_as_a_query_param(token_client):
    route = respx.post(f"{BASE_URL}/accounts/{ACCOUNT}/teams/t1/members").mock(
        return_value=httpx.Response(200, json={})
    )
    await org.hava_add_team_member(team_id="t1", email="new@example.com")
    assert route.calls.last.request.url.params["email"] == "new@example.com"


@respx.mock
async def test_add_project_member_sends_user_id_not_email(token_client):
    """Teams take an email here, projects take a user_id - easy to mix up."""
    route = respx.post(f"{BASE_URL}/accounts/{ACCOUNT}/projects/p1/members").mock(
        return_value=httpx.Response(200, json={})
    )
    await org.hava_add_project_member(project_id="p1", user_id="u1")
    assert route.calls.last.request.url.params["user_id"] == "u1"


@respx.mock
async def test_delete_team_lists_the_members_it_removes(token_client):
    respx.get(f"{BASE_URL}/accounts/{ACCOUNT}/teams/t1").mock(
        return_value=httpx.Response(200, json={"id": "t1", "name": "contractors", "members": [
            {"id": "u1", "email": "a@example.com"}, {"id": "u2", "email": "b@example.com"}]})
    )
    respx.delete(f"{BASE_URL}/accounts/{ACCOUNT}/teams/t1").mock(return_value=httpx.Response(204))
    text = await org.hava_delete_team(team_id="t1", confirm=True)
    assert "contractors" in text and "a@example.com" in text and "b@example.com" in text


async def test_update_project_rejects_an_empty_update(token_client):
    text = await org.hava_update_project(project_id="p1")
    assert text.startswith("Error:") and "nothing to update" in text


@respx.mock
async def test_api_token_listing_reports_metadata_only(token_client):
    respx.get(f"{BASE_URL}/accounts/{ACCOUNT}/tokens").mock(
        return_value=httpx.Response(200, json={"total_size": 1, "results": [
            {"id": "tok1", "name": "CI token", "last_used_at": "2026-10-07T14:14:00Z"}]})
    )
    text = await org.hava_list_api_tokens()
    assert "CI token" in text and "never returned by the API" in text


async def test_revoking_a_token_requires_confirmation(token_client):
    text = await org.hava_delete_api_token(token_id="tok1")
    assert text.startswith("Error:") and "confirm=true" in text


# --------------------------------------------------------------------------- #
# Reports (403 on the plan this was built against)
# --------------------------------------------------------------------------- #

@respx.mock
async def test_report_403_explains_it_is_a_plan_feature(hava_client):
    respx.get(f"{BASE_URL}/reports").mock(return_value=httpx.Response(403))
    text = await reports.hava_list_reports()
    assert "plan feature" in text


@respx.mock
async def test_report_export_sends_pdf_in_query_and_body(hava_client):
    """The spec declares export_format in both places, so send it in both."""
    route = respx.post(f"{BASE_URL}/reports/r1/export").mock(
        return_value=httpx.Response(202, headers={"location": f"/jobs/{JOB}"})
    )
    respx.get(f"{BASE_URL}/jobs/{JOB}").mock(
        return_value=httpx.Response(303, headers={"location": "https://cdn.test/r.pdf"})
    )
    await reports.hava_export_report(report_id="r1")
    request = route.calls.last.request
    assert request.url.params["export_format"] == "pdf"
    assert json.loads(request.content)["export_format"] == "pdf"


@respx.mock
async def test_update_diagram_reports_a_rename_hava_ignored(hava_client):
    """Hava accepts `name` on PUT /environments and silently drops it."""
    respx.put(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "old-name"})
    )
    respx.get(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "old-name"})
    )
    text = await custom_diagrams.hava_update_diagram(
        diagram_id=DIAGRAM, query="vpc:vpc-1", name="new-name")
    assert "ignored the rename" in text and "old-name" in text


@respx.mock
async def test_update_diagram_is_quiet_when_the_rename_sticks(hava_client):
    respx.put(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "new-name"})
    )
    respx.get(f"{BASE_URL}/environments/{DIAGRAM}").mock(
        return_value=httpx.Response(200, json={"id": DIAGRAM, "name": "new-name"})
    )
    data = json.loads(await custom_diagrams.hava_update_diagram(
        diagram_id=DIAGRAM, query="vpc:vpc-1", name="new-name", response_format="json"))
    assert data["rename_applied"] is True


@respx.mock
async def test_remove_team_member_requires_confirmation(token_client):
    """Removing a member revokes a real person's access."""
    route = respx.delete(f"{BASE_URL}/accounts/{ACCOUNT}/teams/t1/members/u1")
    text = await org.hava_remove_team_member(team_id="t1", user_id="u1")
    assert text.startswith("Error:") and "confirm=true" in text
    assert not route.called


@respx.mock
async def test_remove_team_member_proceeds_once_confirmed(token_client):
    route = respx.delete(f"{BASE_URL}/accounts/{ACCOUNT}/teams/t1/members/u1").mock(
        return_value=httpx.Response(204)
    )
    text = await org.hava_remove_team_member(team_id="t1", user_id="u1", confirm=True)
    assert route.called and "Removed user" in text


@respx.mock
async def test_remove_project_member_requires_confirmation(token_client):
    route = respx.delete(f"{BASE_URL}/accounts/{ACCOUNT}/projects/p1/members/u1")
    text = await org.hava_remove_project_member(project_id="p1", user_id="u1")
    assert text.startswith("Error:") and "confirm=true" in text
    assert not route.called


async def test_every_destructive_tool_has_a_confirm_parameter():
    """Mirrors scripts/check_skill.py, so a regression fails the suite too."""
    from hava_mcp.server import mcp

    tools = await mcp.list_tools()
    missing = [
        t.name for t in tools
        if t.annotations.destructiveHint
        and "confirm" not in (t.inputSchema or {}).get("properties", {})
    ]
    assert not missing, f"destructive tools without a confirm guard: {missing}"
