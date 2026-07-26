from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse, urlunparse


DEFAULT_API_BASE = "https://api.agenrena.com/api/agent-api"
DEFAULT_USER_AGENT = (
    "agenrena-codex-bridge/0.3.0 agenrena-hermes-adapter/0.4.0"
)


class ConfigurationError(RuntimeError):
    pass


def bridge_config_dir(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> Path:
    explicit_dir = str(environ.get("AGENRENA_BRIDGE_CONFIG_DIR") or "").strip()
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    xdg_config_home = str(environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "agenrena-codex-bridge"

    resolved_home = home if home is not None else Path.home()
    return resolved_home / ".config" / "agenrena-codex-bridge"


def bridge_config_path(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> Path:
    explicit_file = str(
        environ.get("AGENRENA_BRIDGE_CONFIG_FILE") or ""
    ).strip()
    if explicit_file:
        return Path(explicit_file).expanduser()
    return bridge_config_dir(environ, home=home) / "config.json"


def load_bridge_config(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    path = bridge_config_path(environ, home=home)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigurationError(f"Could not read bridge config at {path}: {error}") from error

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"Bridge config at {path} is not valid JSON.") from error
    if not isinstance(value, dict):
        raise ConfigurationError(f"Bridge config at {path} must be a JSON object.")
    return value


def bridge_state_dir(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
    bridge_config: Optional[Mapping[str, Any]] = None,
) -> Path:
    explicit_dir = str(environ.get("BRIDGE_STATE_DIR") or "").strip()
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    configured_dir = str((bridge_config or {}).get("state_dir") or "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser()

    xdg_state_home = str(environ.get("XDG_STATE_HOME") or "").strip()
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "agenrena-codex-bridge"

    resolved_home = home if home is not None else Path.home()
    return resolved_home / ".local" / "state" / "agenrena-codex-bridge"


def credentials_path(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
    bridge_config: Optional[Mapping[str, Any]] = None,
) -> Path:
    explicit_file = str(environ.get("AGENRENA_CREDENTIALS_FILE") or "").strip()
    if explicit_file:
        return Path(explicit_file).expanduser()

    configured_file = str((bridge_config or {}).get("credentials_file") or "").strip()
    if configured_file:
        return Path(configured_file).expanduser()

    explicit_dir = str(environ.get("AGENRENA_CONFIG_DIR") or "").strip()
    if explicit_dir:
        return Path(explicit_dir).expanduser() / "credentials.json"

    configured_dir = str((bridge_config or {}).get("credentials_dir") or "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser() / "credentials.json"

    xdg_config_home = str(environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "agenrena" / "credentials.json"

    resolved_home = home if home is not None else Path.home()
    return resolved_home / ".config" / "agenrena" / "credentials.json"


def load_api_key(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigurationError(
            f"Agenrena credentials were not found at {path}. "
            "Run `agenrena auth login` or set AGENRENA_CONFIG_DIR."
        ) from error
    except OSError as error:
        raise ConfigurationError(
            f"Could not read Agenrena credentials at {path}: {error}"
        ) from error

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Agenrena credentials at {path} are not valid JSON."
        ) from error

    api_key = str(value.get("api_key") or "").strip() if isinstance(value, dict) else ""
    if not api_key:
        raise ConfigurationError(
            f"Agenrena credentials at {path} do not contain api_key."
        )
    if not api_key.startswith("agr_"):
        raise ConfigurationError(
            f"Agenrena credentials at {path} contain an invalid API key."
        )
    return api_key


def derive_ws_url(api_base: str) -> str:
    parsed = urlparse(api_base)
    if not parsed.hostname:
        raise ConfigurationError("AGENRENA_API_BASE must be an absolute URL.")
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        raise ConfigurationError("AGENRENA_API_BASE must use http or https.")

    return urlunparse((scheme, parsed.netloc, "/ws/agent/events/", "", "", ""))


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer.") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return parsed


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_base: str
    ws_url: str
    codex_bin: str
    codex_workspace: Path
    codex_model: Optional[str]
    codex_sandbox_mode: str
    codex_approval_policy: str
    codex_turn_timeout_seconds: int
    state_dir: Path
    log_level: str
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] = os.environ,
        cwd: Optional[Path] = None,
        home: Optional[Path] = None,
    ) -> "Settings":
        project_dir = cwd if cwd is not None else Path.cwd()
        bridge_config = load_bridge_config(environ, home=home)
        credential_file = credentials_path(
            environ,
            home=home,
            bridge_config=bridge_config,
        )
        api_key = load_api_key(credential_file)

        api_base = str(
            environ.get("AGENRENA_API_BASE")
            or bridge_config.get("api_base")
            or DEFAULT_API_BASE
        ).rstrip("/")
        ws_url = str(
            environ.get("AGENRENA_WS_URL")
            or bridge_config.get("ws_url")
            or derive_ws_url(api_base)
        ).strip()
        parsed_ws = urlparse(ws_url)
        if parsed_ws.scheme != "wss" or not parsed_ws.hostname:
            raise ConfigurationError(
                "AGENRENA_WS_URL must be an absolute wss:// URL."
            )

        workspace = Path(
            str(
                environ.get("CODEX_WORKSPACE")
                or bridge_config.get("workspace")
                or project_dir
            )
        ).expanduser().resolve()
        if not workspace.is_dir():
            raise ConfigurationError(f"CODEX_WORKSPACE is not a directory: {workspace}")

        state_dir = bridge_state_dir(
            environ,
            home=home,
            bridge_config=bridge_config,
        )

        return cls(
            api_key=api_key,
            api_base=api_base,
            ws_url=ws_url,
            codex_bin=str(
                environ.get("CODEX_BIN")
                or bridge_config.get("codex_bin")
                or "codex"
            ),
            codex_workspace=workspace,
            codex_model=str(
                environ.get("CODEX_MODEL")
                or bridge_config.get("codex_model")
                or ""
            ).strip()
            or None,
            codex_sandbox_mode=str(
                environ.get("CODEX_SANDBOX_MODE")
                or bridge_config.get("codex_sandbox_mode")
                or "read-only"
            ).strip(),
            codex_approval_policy=str(
                environ.get("CODEX_APPROVAL_POLICY")
                or bridge_config.get("codex_approval_policy")
                or "never"
            ).strip(),
            codex_turn_timeout_seconds=_positive_int(
                str(
                    environ.get("CODEX_TURN_TIMEOUT_SECONDS")
                    or bridge_config.get("codex_turn_timeout_seconds")
                    or "900"
                ),
                "CODEX_TURN_TIMEOUT_SECONDS",
            ),
            state_dir=state_dir,
            log_level=str(
                environ.get("LOG_LEVEL")
                or bridge_config.get("log_level")
                or "INFO"
            ).upper(),
            user_agent=str(
                environ.get("AGENRENA_USER_AGENT")
                or bridge_config.get("user_agent")
                or DEFAULT_USER_AGENT
            ),
        )
