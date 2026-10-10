"""Exercise Claude's discovery-to-initialize fallback on a real subprocess."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROBE = Path(__file__).resolve().parents[1] / "scripts" / "probe_tools.py"


@pytest.mark.parametrize("discovery,delayed", [(False, False), (True, False), (True, True)])
def test_stdio_handshake_and_tool_call(discovery, delayed):
    command = [sys.executable, str(PROBE)]
    if discovery:
        command.append("--discover-first")
    if delayed:
        # Claude's 2s discovery timer has expired by the time these queued
        # requests reach the server. Both must be safe on the same connection.
        command += ["--pipeline", "--", sys.executable, "-c",
                    "import time; time.sleep(2.2); from hava_mcp.cli import main; main()"]
    env = {k: v for k, v in os.environ.items() if k != "HAVA_MCP_READ_ONLY"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=40)
    assert completed.returncode == 0, completed.stderr
    tools = json.loads(completed.stdout)
    assert len(tools) == 44
    assert "hava_list_diagrams" in {tool["name"] for tool in tools}
