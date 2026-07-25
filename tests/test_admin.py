from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agenrena_codex_bridge.admin import configure_bridge, public_bridge_config
from agenrena_codex_bridge.config import ConfigurationError


class AdminTests(unittest.TestCase):
    def test_configure_bridge_stores_locations_but_not_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            secret = "agr_test_secret"
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"version": 1, "api_key": secret}),
                encoding="utf-8",
            )
            workspace = root / "project"
            workspace.mkdir()
            bridge_config_dir = root / "bridge-config"
            environ = {
                "AGENRENA_BRIDGE_CONFIG_DIR": str(bridge_config_dir),
            }

            result = configure_bridge(
                workspace=str(workspace),
                credentials_dir=str(credentials_dir),
                api_base="http://localhost:8020/api/agent-api",
                ws_url="wss://localhost:8020/ws/agent/events/",
                environ=environ,
                home=root,
            )

            config_path = Path(result["config_file"])
            raw = config_path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            self.assertEqual(os.stat(config_path).st_mode & 0o777, 0o600)
            value = json.loads(raw)
            self.assertEqual(value["workspace"], str(workspace.resolve()))
            self.assertEqual(
                value["credentials_dir"],
                str(credentials_dir.resolve()),
            )
            self.assertEqual(
                value["ws_url"],
                "wss://localhost:8020/ws/agent/events/",
            )

            public = public_bridge_config(environ=environ, home=root)
            self.assertTrue(public["configured"])
            self.assertNotIn("api_key", public)

    def test_configure_bridge_rejects_invalid_api_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"api_key": "agr_test_secret"}),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigurationError):
                configure_bridge(
                    workspace=str(root),
                    credentials_dir=str(credentials_dir),
                    api_base="not-a-url",
                    ws_url="wss://localhost:8020/ws/agent/events/",
                    environ={
                        "AGENRENA_BRIDGE_CONFIG_DIR": str(root / "bridge"),
                    },
                    home=root,
                )

    def test_configure_bridge_rejects_unencrypted_websocket(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"api_key": "agr_test_secret"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "wss://"):
                configure_bridge(
                    workspace=str(root),
                    credentials_dir=str(credentials_dir),
                    ws_url="ws://localhost:8020/ws/agent/events/",
                    environ={
                        "AGENRENA_BRIDGE_CONFIG_DIR": str(root / "bridge"),
                    },
                    home=root,
                )


if __name__ == "__main__":
    unittest.main()
