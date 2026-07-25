from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from .config import (
    DEFAULT_API_BASE,
    ConfigurationError,
    bridge_config_path,
    credentials_path,
    derive_ws_url,
    load_api_key,
    load_bridge_config,
)


def configure_bridge(
    *,
    workspace: str,
    credentials_dir: Optional[str] = None,
    api_base: Optional[str] = None,
    ws_url: Optional[str] = None,
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ConfigurationError(
            f"The requested Codex workspace is not a directory: {resolved_workspace}"
        )

    current = load_bridge_config(environ, home=home)
    next_config = dict(current)
    next_config["version"] = 1
    next_config["workspace"] = str(resolved_workspace)
    if credentials_dir is not None:
        resolved_credentials_dir = Path(credentials_dir).expanduser().resolve()
        next_config["credentials_dir"] = str(resolved_credentials_dir)
        next_config.pop("credentials_file", None)
    if api_base is not None:
        next_config["api_base"] = api_base.rstrip("/")
    if ws_url is not None:
        next_config["ws_url"] = ws_url

    resolved_api_base = str(next_config.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    parsed_api = urlparse(resolved_api_base)
    if parsed_api.scheme not in {"http", "https"} or not parsed_api.hostname:
        raise ConfigurationError(
            "The Agenrena API base must be an absolute http:// or https:// URL."
        )
    resolved_ws_url = str(next_config.get("ws_url") or derive_ws_url(resolved_api_base))
    parsed_ws = urlparse(resolved_ws_url)
    if parsed_ws.scheme != "wss" or not parsed_ws.hostname:
        raise ConfigurationError(
            "The Agenrena WebSocket URL must use wss://."
        )
    next_config["api_base"] = resolved_api_base
    next_config["ws_url"] = resolved_ws_url

    credential_file = credentials_path(
        environ,
        home=home,
        bridge_config=next_config,
    )
    load_api_key(credential_file)

    path = bridge_config_path(environ, home=home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(next_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)

    return {
        "config_file": str(path),
        "workspace": str(resolved_workspace),
        "credentials_file": str(credential_file),
        "api_base": resolved_api_base,
        "ws_url": resolved_ws_url,
    }


def public_bridge_config(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    path = bridge_config_path(environ, home=home)
    value = load_bridge_config(environ, home=home)
    return {
        "configured": bool(value.get("workspace")),
        "config_file": str(path),
        "workspace": value.get("workspace"),
        "credentials_dir": value.get("credentials_dir"),
        "credentials_file": value.get("credentials_file"),
        "api_base": value.get("api_base") or DEFAULT_API_BASE,
        "ws_url": value.get("ws_url"),
        "sandbox_mode": value.get("codex_sandbox_mode") or "read-only",
        "approval_policy": value.get("codex_approval_policy") or "never",
    }
