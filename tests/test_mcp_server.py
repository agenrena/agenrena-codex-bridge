from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from agenrena_codex_bridge.mcp_server import TOOLS, handle_request


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "agenrena-codex-bridge"


class McpServerTests(unittest.TestCase):
    def test_tools_are_management_only(self):
        names = {tool["name"] for tool in TOOLS}
        self.assertEqual(
            names,
            {
                "agenrena_bridge_setup",
                "agenrena_bridge_start",
                "agenrena_bridge_status",
                "agenrena_bridge_stop",
            },
        )
        self.assertNotIn("send_message", " ".join(sorted(names)))

    def test_initialize_and_list_tools(self):
        initialized = handle_request(
            {
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(initialized["protocolVersion"], "2025-06-18")

        listed = handle_request({"method": "tools/list"})
        self.assertEqual(listed["tools"], TOOLS)

    def test_stdio_entrypoint_returns_json_rpc_response(self):
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )
        environ = dict(os.environ)
        environ["PYTHONPATH"] = str(PLUGIN_ROOT / "src")
        completed = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "mcp-server.py")],
            input=request + "\n",
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environ,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        response = json.loads(completed.stdout)
        self.assertEqual(response["id"], 1)
        self.assertEqual(len(response["result"]["tools"]), 4)


if __name__ == "__main__":
    unittest.main()
