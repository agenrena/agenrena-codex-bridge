from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping

from .admin import configure_bridge, public_bridge_config
from .config import ConfigurationError
from .process import get_process_status, start_daemon, stop_daemon


SERVER_INFO = {
    "name": "agenrena-codex-bridge",
    "title": "Agenrena Codex Bridge",
    "version": "0.2.0",
}

TOOLS = [
    {
        "name": "agenrena_bridge_setup",
        "description": (
            "Configure the local Agenrena-to-Codex bridge for one explicit Codex "
            "workspace. This reads an existing Agenrena CLI credentials file; it "
            "does not accept or store an API key."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workspace"],
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": (
                        "Absolute local directory that Codex should use as cwd for "
                        "Agenrena conversations."
                    ),
                },
                "credentialsDir": {
                    "type": "string",
                    "description": (
                        "Optional directory containing Agenrena credentials.json. "
                        "Omit to use the Agenrena CLI default."
                    ),
                },
                "apiBase": {
                    "type": "string",
                    "description": (
                        "Optional Agent API base, for example "
                        "http://localhost:8020/api/agent-api."
                    ),
                },
                "wsUrl": {
                    "type": "string",
                    "description": (
                        "Optional wss:// Agent WebSocket endpoint. It must not "
                        "contain the API key; the bridge adds the token."
                    ),
                },
            },
        },
    },
    {
        "name": "agenrena_bridge_start",
        "description": (
            "Start the configured background bridge. It receives Agenrena text "
            "events, runs Codex app-server, and replies to the same conversation."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
    {
        "name": "agenrena_bridge_status",
        "description": (
            "Show the local bridge configuration and whether its background "
            "process is running. Never returns the API key."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
    {
        "name": "agenrena_bridge_stop",
        "description": "Stop the local Agenrena Codex Bridge background process.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
]


def _text_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, indent=2
    )
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def call_tool(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    try:
        if name == "agenrena_bridge_setup":
            process = get_process_status()
            if process["running"]:
                raise ConfigurationError(
                    "Stop the Agenrena bridge before changing its workspace or endpoints."
                )
            configured = configure_bridge(
                workspace=str(arguments.get("workspace") or ""),
                credentials_dir=(
                    str(arguments["credentialsDir"])
                    if arguments.get("credentialsDir")
                    else None
                ),
                api_base=(
                    str(arguments["apiBase"])
                    if arguments.get("apiBase")
                    else None
                ),
                ws_url=(
                    str(arguments["wsUrl"])
                    if arguments.get("wsUrl")
                    else None
                ),
            )
            return _text_result(
                {
                    "configured": True,
                    **configured,
                    "next_step": "Call agenrena_bridge_start.",
                }
            )

        if name == "agenrena_bridge_start":
            status = start_daemon()
            return _text_result(
                {
                    **status,
                    "message": (
                        "Agenrena text messages will be answered through Codex "
                        "while this bridge is running."
                    ),
                }
            )

        if name == "agenrena_bridge_status":
            return _text_result(
                {
                    "config": public_bridge_config(),
                    "process": get_process_status(),
                }
            )

        if name == "agenrena_bridge_stop":
            return _text_result(stop_daemon())

        return _text_result(f"Unknown tool: {name}", is_error=True)
    except Exception as error:
        return _text_result(str(error), is_error=True)


def handle_request(message: Mapping[str, Any]) -> Any:
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, Mapping):
        params = {}

    if method == "initialize":
        return {
            "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        return call_tool(
            name,
            arguments if isinstance(arguments, Mapping) else {},
        )
    raise RuntimeError(f"Unsupported MCP method: {method}")


def main() -> None:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, Mapping):
                continue
        except json.JSONDecodeError:
            continue

        if "id" not in message:
            continue
        request_id = message.get("id")
        try:
            result = handle_request(message)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": str(error),
                },
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
