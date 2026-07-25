from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from agenrena_codex_bridge.admin import configure_bridge
from agenrena_codex_bridge.process import (
    get_process_status,
    start_daemon,
    stop_daemon,
)


class ProcessTests(unittest.TestCase):
    def test_background_bridge_starts_and_stops_without_persisting_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            secret = "agr_process_test_secret"
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"api_key": secret}),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            workspace.mkdir()
            environ = {
                "AGENRENA_BRIDGE_CONFIG_DIR": str(root / "config"),
                "BRIDGE_STATE_DIR": str(root / "state"),
                "CODEX_BIN": sys.executable,
            }
            configure_bridge(
                workspace=str(workspace),
                credentials_dir=str(credentials_dir),
                api_base="http://127.0.0.1:1/api/agent-api",
                ws_url="wss://127.0.0.1:1/ws/agent/events/",
                environ=environ,
                home=root,
            )

            try:
                started = start_daemon(environ=environ, home=root)
                self.assertTrue(started["running"])
                self.assertIsInstance(started["pid"], int)
                process_file = root / "state" / "process.json"
                self.assertNotIn(
                    secret,
                    process_file.read_text(encoding="utf-8"),
                )
            finally:
                stopped = stop_daemon(environ=environ, home=root)

            self.assertFalse(stopped["running"])
            self.assertFalse(get_process_status(environ=environ, home=root)["running"])


if __name__ == "__main__":
    unittest.main()
