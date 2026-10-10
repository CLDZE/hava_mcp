#!/usr/bin/env python3
"""Probe real stdio negotiation and print manifest-ready tools (no Hava API calls)."""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
import tempfile


async def probe(command: list[str], *, discover_first: bool, pipeline: bool, timeout: float) -> list[dict]:
    env = dict(os.environ, HAVA_API_TOKEN="", HAVA_ENDPOINT="https://api.hava.invalid")
    # A file avoids a full stderr pipe blocking the server during package setup.
    with tempfile.TemporaryFile() as errors:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=errors, env=env, limit=4 * 1024 * 1024,
        )

        async def send(method: str, params: dict, request_id: int | None) -> None:
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if request_id is not None:
                message["id"] = request_id
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def receive(request_id: int) -> dict:
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout)
                if not line:
                    raise RuntimeError("Server closed stdout before replying")
                response = json.loads(line)
                if response.get("id") == request_id:
                    return response
                if "id" in response:
                    raise RuntimeError(f"Unexpected response: {response}")

        def result(response: dict) -> dict:
            if "error" in response:
                raise RuntimeError(f"Protocol error: {response['error']}")
            return response["result"]

        try:
            if discover_first:
                await send("server/discover", {"_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }}, 0)
                if not pipeline:
                    discovery = await receive(0)
                    assert "error" in discovery, "Compatibility release must reject modern discovery"
            await send("initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "hava-package-probe", "version": "1"},
            }, 1)
            if discover_first and pipeline:
                discovery = await receive(0)
                assert "error" in discovery, "Compatibility release must reject modern discovery"
            initialized = result(await receive(1))
            assert initialized["protocolVersion"] == "2025-11-25", initialized
            assert initialized["serverInfo"]["name"] == "hava_mcp", initialized
            await send("notifications/initialized", {}, None)
            await send("tools/list", {}, 2)
            tools = result(await receive(2))["tools"]
            assert tools, "Server exposed no tools"
            await send("tools/call", {"name": "hava_auth_status", "arguments": {"verify": False}}, 3)
            auth = result(await receive(3))
            assert not auth.get("isError"), auth
            assert any("no API token configured" in c.get("text", "") for c in auth["content"]), auth
            return tools
        except Exception:
            errors.seek(0)
            print(errors.read().decode(errors="replace")[-6000:], file=sys.stderr)
            raise
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover-first", action="store_true")
    parser.add_argument("--pipeline", action="store_true", help="Send initialize before reading discovery's reply")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    tools = asyncio.run(probe(
        command or [sys.executable, "-m", "hava_mcp"],
        discover_first=args.discover_first, pipeline=args.pipeline, timeout=args.timeout,
    ))
    json.dump([
        {"name": t["name"], "description": (t.get("description") or "").strip().split("\n")[0]}
        for t in sorted(tools, key=lambda t: t["name"])
    ], sys.stdout, indent=2)


if __name__ == "__main__":
    main()
