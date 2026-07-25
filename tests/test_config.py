from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agenrena_codex_bridge.config import (
    ConfigurationError,
    Settings,
    bridge_config_path,
    credentials_path,
    derive_ws_url,
)


class ConfigTests(unittest.TestCase):
    def test_credentials_path_supports_specific_config_directory(self):
        path = credentials_path(
            {"AGENRENA_CONFIG_DIR": "/private/agent-config"},
            home=Path("/unused"),
        )
        self.assertEqual(
            path, Path("/private/agent-config/credentials.json")
        )

    def test_settings_load_existing_cli_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "credentials.json").write_text(
                json.dumps({"version": 1, "api_key": "agr_test_secret"}),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            workspace.mkdir()

            settings = Settings.from_env(
                {
                    "AGENRENA_CONFIG_DIR": str(config_dir),
                    "AGENRENA_API_BASE": "http://localhost:8020/api/agent-api",
                    "AGENRENA_WS_URL": "wss://localhost:8020/ws/agent/events/",
                    "CODEX_WORKSPACE": str(workspace),
                },
                cwd=root,
                home=root,
            )

            self.assertEqual(settings.api_key, "agr_test_secret")
            self.assertEqual(
                settings.ws_url,
                "wss://localhost:8020/ws/agent/events/",
            )
            self.assertEqual(settings.codex_workspace, workspace.resolve())
            self.assertEqual(settings.codex_sandbox_mode, "read-only")

    def test_saved_bridge_config_is_used_without_codex_workspace_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"api_key": "agr_test_secret"}),
                encoding="utf-8",
            )
            workspace = root / "saved-workspace"
            workspace.mkdir()
            environ = {
                "AGENRENA_BRIDGE_CONFIG_DIR": str(root / "bridge-config"),
            }
            config_path = bridge_config_path(environ, home=root)
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "credentials_dir": str(credentials_dir),
                        "api_base": "http://localhost:8020/api/agent-api",
                        "ws_url": "wss://localhost:8020/ws/agent/events/",
                    }
                ),
                encoding="utf-8",
            )

            settings = Settings.from_env(environ, cwd=root, home=root)

            self.assertEqual(settings.codex_workspace, workspace.resolve())
            self.assertEqual(settings.api_key, "agr_test_secret")
            self.assertEqual(
                settings.state_dir,
                root / ".local" / "state" / "agenrena-codex-bridge",
            )

    def test_environment_overrides_saved_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials_dir = root / "agenrena"
            credentials_dir.mkdir()
            (credentials_dir / "credentials.json").write_text(
                json.dumps({"api_key": "agr_test_secret"}),
                encoding="utf-8",
            )
            saved_workspace = root / "saved"
            env_workspace = root / "from-env"
            saved_workspace.mkdir()
            env_workspace.mkdir()
            environ = {
                "AGENRENA_BRIDGE_CONFIG_DIR": str(root / "bridge-config"),
                "CODEX_WORKSPACE": str(env_workspace),
            }
            config_path = bridge_config_path(environ, home=root)
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        "workspace": str(saved_workspace),
                        "credentials_dir": str(credentials_dir),
                    }
                ),
                encoding="utf-8",
            )

            settings = Settings.from_env(environ, cwd=root, home=root)

            self.assertEqual(settings.codex_workspace, env_workspace.resolve())

    def test_invalid_key_is_rejected_without_echoing_the_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential_file = root / "credentials.json"
            credential_file.write_text(
                json.dumps({"api_key": "not-a-valid-agent-key"}),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError) as raised:
                Settings.from_env(
                    {
                        "AGENRENA_CREDENTIALS_FILE": str(credential_file),
                        "CODEX_WORKSPACE": str(root),
                    },
                    cwd=root,
                )
            self.assertNotIn("not-a-valid-agent-key", str(raised.exception))

    def test_settings_reject_unencrypted_websocket_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential_file = root / "credentials.json"
            credential_file.write_text(
                json.dumps({"api_key": "agr_test_secret"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "wss://"):
                Settings.from_env(
                    {
                        "AGENRENA_CREDENTIALS_FILE": str(credential_file),
                        "AGENRENA_WS_URL": "ws://api.agenrena.com/ws/agent/events/",
                        "CODEX_WORKSPACE": str(root),
                    },
                    cwd=root,
                )

    def test_ws_url_is_derived_from_api_host_not_api_path(self):
        self.assertEqual(
            derive_ws_url("https://api.agenrena.com/api/agent-api"),
            "wss://api.agenrena.com/ws/agent/events/",
        )


if __name__ == "__main__":
    unittest.main()
