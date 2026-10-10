"""Managing connected cloud sources.

Deliberately missing: ``POST /sources`` and credential rotation via
``PUT /sources/{id}``. Both require long-lived cloud secrets (AWS secret keys,
Azure client secrets, GCP service-account JSON), and routing those through an
LLM conversation would put them in a transcript. Connect and re-credential
sources in the Hava UI; ``hava_rename_source`` covers the safe part of PUT.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..app import (
    client,
    hava_tool,
    job_from_response,
    poll_job,
    require_confirm,
)
from ..errors import HavaError
from ..formatting import (
    ResponseFormat,
    as_output,
    render_simple,
    render_source_detail,
    summarize_source_detail,
)


@hava_tool("hava_get_source", title="Get a cloud source")
async def hava_get_source(
    source_id: Annotated[str, Field(description="The source's UUID, from hava_list_sources.", min_length=1)],
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'.")] = ResponseFormat.MARKDOWN,
) -> str:
    """Get one cloud source, including when it last synced.

    Use this to check whether a source's data is current before trusting its
    diagrams, and to see the role or credential identifier Hava connects with.

    Args:
        source_id: The source UUID.
        response_format: 'markdown' (default) or 'json'.

    Returns:
        str: The source's type, cloud account, state, and last/queued sync times.
    """
    current = client()
    payload = await current.get_json(f"/sources/{source_id}")
    source = summarize_source_detail(payload or {})
    return as_output({"source": source}, render_source_detail(source), response_format)


@hava_tool(
    "hava_sync_source",
    title="Sync a cloud source",
    read_only=False,
    idempotent=False,
)
async def hava_sync_source(
    source_id: Annotated[str, Field(description="The source's UUID.", min_length=1)],
    metadata: Annotated[
        dict | None,
        Field(default=None, description="Optional metadata attached to the job and to the resources/diagrams it creates, e.g. {\"build_no\": 10}."),
    ] = None,
    wait_seconds: Annotated[
        int,
        Field(default=0, description="Seconds to wait for the import. 0 returns the job ID immediately, which is usually right - a full import can take many minutes.", ge=0, le=900),
    ] = 0,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'.")] = ResponseFormat.MARKDOWN,
) -> str:
    """Re-import a cloud account so its diagrams reflect current infrastructure.

    This calls out to the cloud provider and rebuilds every diagram for the
    source, which can take many minutes on a large account and creates a new
    version in each diagram's history. It does not change any cloud resources.

    Defaults to returning the job ID rather than waiting, because imports
    usually outlast any sensible tool timeout. Poll with `hava_get_job`, or
    check `last_sync` with `hava_get_source`.

    Args:
        source_id: The source UUID.
        metadata: Arbitrary metadata to attach - handy from CI to record a build
            number against the diagrams it produced.
        wait_seconds: Polling budget; 0 (default) returns immediately.
        response_format: 'markdown' (default) or 'json'.

    Returns:
        str: The job ID and state.

    Examples:
        - Use when: "refresh the diagrams, we just deployed"
        - Don't use when: the diagram is merely slow to read - syncing is
          expensive and adds version history.
    """
    current = client()
    response = await current.request(
        "POST", f"/sources/{source_id}/sync", json=metadata or None
    )
    job_id = job_from_response(response)
    state = "queued"
    if job_id and wait_seconds:
        state, _ = await poll_job(current, job_id, wait_seconds)

    result = {"source_id": source_id, "job_id": job_id, "state": state}
    note = (
        f"Import queued. Check progress with hava_get_job(job_id=\"{job_id}\") "
        f"or hava_get_source(source_id=\"{source_id}\")."
        if job_id else
        "Hava accepted the sync but returned no job reference; check "
        "hava_get_source for the sync timestamps."
    )
    return as_output(result, render_simple(f"Source sync {state}", {
        "source_id": f"`{source_id}`",
        "job_id": f"`{job_id}`" if job_id else None,
        "metadata": metadata or None,
    }, note=note), response_format)


@hava_tool(
    "hava_rename_source",
    title="Rename a cloud source",
    read_only=False,
    idempotent=True,
)
async def hava_rename_source(
    source_id: Annotated[str, Field(description="The source's UUID.", min_length=1)],
    name: Annotated[str, Field(description="The new display name for the source.", min_length=1)],
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'.")] = ResponseFormat.MARKDOWN,
) -> str:
    """Rename a connected cloud source.

    This is the only part of `PUT /sources/{id}` exposed here. The same endpoint
    can rotate cloud credentials, which is intentionally not available through
    this server - do that in the Hava UI so secrets never enter a conversation.

    Args:
        source_id: The source UUID.
        name: The new display name.
        response_format: 'markdown' (default) or 'json'.

    Returns:
        str: The updated source.
    """
    current = client()
    response = await current.request("PUT", f"/sources/{source_id}", json={"name": name})
    updated = {}
    if response.content:
        try:
            updated = response.json() or {}
        except ValueError:
            updated = {}
    source = summarize_source_detail(updated or {"id": source_id, "display_name": name})
    return as_output({"source": source}, render_source_detail(source), response_format)


@hava_tool(
    "hava_delete_source",
    title="Delete a cloud source",
    read_only=False,
    destructive=True,
    idempotent=True,
)
async def hava_delete_source(
    source_id: Annotated[str, Field(description="The source's UUID.", min_length=1)],
    confirm: Annotated[
        bool,
        Field(default=False, description="Must be true. This also deletes every resource and diagram from this source."),
    ] = False,
) -> str:
    """Disconnect a cloud source, deleting all its resources and diagrams.

    This is the most destructive call in the server: it removes the source and
    **every diagram and version history derived from it**. It does not touch
    anything in the cloud account itself, but the Hava-side history is gone for
    good. Name the source and its diagram count to the user and get explicit
    agreement before calling.

    Args:
        source_id: The source UUID.
        confirm: Must be true.

    Returns:
        str: Confirmation naming what was removed.
    """
    require_confirm(confirm, f"delete source {source_id} and all of its diagrams")
    current = client()
    name = None
    try:
        existing = await current.get_json(f"/sources/{source_id}")
        name = (existing or {}).get("display_name") or (existing or {}).get("name")
    except HavaError:
        pass
    await current.request("DELETE", f"/sources/{source_id}")
    label = f"{name!r} (`{source_id}`)" if name else f"`{source_id}`"
    return (
        f"Deleted source {label}, along with its resources and diagrams. "
        f"The cloud account itself was not touched. This cannot be undone."
    )
