# Agenrena Codex Bridge

A Codex plugin that connects Agenrena Agent text, image, and sticker messages
to a local Codex project:

```text
Agenrena Agent WebSocket
        ↓
Codex app-server
        ↓
Agenrena Agent HTTP reply API
```

Inbound messages may contain text, images, or a sticker. An Agenrena message
starts or resumes a native Codex thread, downloaded media is supplied through
Codex `localImage` inputs, and Codex's final text answer is sent back to that
same conversation. The plugin does not expose a tool for initiating arbitrary
Agenrena messages.

When present, the sender's Agenrena ID and display name are supplied to Codex
as a separate, JSON-encoded metadata text item before the user text or media.
The values are explicitly labeled as untrusted data, not instructions. This
metadata becomes part of the native Codex thread history. Messages without
sender data continue without a metadata item.

See [PLAN.md](PLAN.md) for the protocol contracts and later phases.

## Plugin contents

- `.agents/plugins/marketplace.json`: installable Agenrena marketplace entry.
- `plugins/agenrena-codex-bridge/.codex-plugin/plugin.json`: Codex plugin
  manifest.
- `plugins/agenrena-codex-bridge/.mcp.json`: local management MCP server
  registration.
- `plugins/agenrena-codex-bridge/skills/agenrena-bridge`: instructions for
  Codex to configure and operate the bridge safely.
- `plugins/agenrena-codex-bridge/scripts/mcp-server.py`: MCP entry point.
- `plugins/agenrena-codex-bridge/src/agenrena_codex_bridge`: the WebSocket,
  Codex app-server, reply API, and background-process implementation.

The MCP server exposes only:

- `agenrena_bridge_setup`
- `agenrena_bridge_start`
- `agenrena_bridge_status`
- `agenrena_bridge_stop`

It deliberately has no `send_message` tool.

## Install from GitHub

Python 3.9 or newer and the `codex` executable are required. The plugin runtime
uses only the Python standard library, so users do not need to create a virtual
environment or run `pip install`.

Publish this repository to GitHub, then install it as a Codex marketplace:

```bash
codex plugin marketplace add OWNER/agenrena-codex-bridge --ref main
codex plugin add agenrena-codex-bridge@agenrena
```

The full GitHub URL also works:

```bash
codex plugin marketplace add \
  https://github.com/OWNER/agenrena-codex-bridge \
  --ref main
codex plugin add agenrena-codex-bridge@agenrena
```

For local development, replace the GitHub source with the repository path:

```bash
codex plugin marketplace add /absolute/path/to/agenrena-codex-bridge
codex plugin add agenrena-codex-bridge@agenrena
```

After installation, start a new Codex thread so Codex loads its skill and MCP
tools. Ask:

```text
Connect this Codex project to Agenrena.
```

Codex will use the current project's absolute directory as the workspace,
validate the existing Agenrena credentials, save non-secret configuration, and
start the background bridge.

## Credentials

By default, setup reads the same credential file used by the Agenrena CLI:

```text
~/.config/agenrena/credentials.json
```

Expected content:

```json
{
  "version": 1,
  "auth_type": "api_key",
  "api_key": "agr_..."
}
```

If it is missing, run:

```bash
agenrena auth login
```

For a different Agenrena CLI config directory, provide that directory during
plugin setup. Environment overrides are also supported:

```bash
export AGENRENA_CONFIG_DIR=/path/to/agenrena-config
export AGENRENA_CREDENTIALS_FILE=/secure/path/credentials.json
```

The API key is used only for the WebSocket token and Agent API Bearer
authentication. It is never copied into the bridge config, process file, or
logs.

## Local files

The plugin stores non-secret configuration at:

```text
~/.config/agenrena-codex-bridge/config.json
```

Runtime status, logs, Codex thread mappings, deduplication state, and temporary
inbound media are stored at:

```text
~/.local/state/agenrena-codex-bridge/
```

They can be redirected with `AGENRENA_BRIDGE_CONFIG_DIR`,
`AGENRENA_BRIDGE_CONFIG_FILE`, `XDG_CONFIG_HOME`, `BRIDGE_STATE_DIR`, or
`XDG_STATE_HOME`.

Inbound media is limited to nine images, 20 MiB per image, and 50 MiB total per
message. The bridge accepts public HTTPS image URLs, rejects non-public network
targets, validates image signatures, and removes temporary files after each
Codex turn.

## Codex safety defaults

Phase 1 cannot answer remote approval prompts, so the background Codex process
uses:

```text
CODEX_SANDBOX_MODE=read-only
CODEX_APPROVAL_POLICY=never
```

Do not use an approval policy that can pause for local interaction until a
remote approval flow exists.

Optional environment overrides:

```bash
export CODEX_BIN=codex
export CODEX_MODEL=
export CODEX_TURN_TIMEOUT_SECONDS=900
export LOG_LEVEL=INFO
```

`CODEX_WORKSPACE` is still accepted for standalone use, but normal plugin setup
saves the selected workspace in the bridge config, so another user does not
need to export it on every launch.

## Standalone mode

The underlying bridge can still be run without Codex plugin management:

```bash
export CODEX_WORKSPACE=/absolute/path/to/project
PYTHONPATH=plugins/agenrena-codex-bridge/src \
  python3 -m agenrena_codex_bridge.main
```

To select another Agenrena environment, its WebSocket endpoint must still use
TLS:

```bash
export AGENRENA_API_BASE=https://staging-api.agenrena.com/api/agent-api
export AGENRENA_WS_URL=wss://staging-api.agenrena.com/ws/agent/events/
```

Production defaults are:

```text
AGENRENA_API_BASE=https://api.agenrena.com/api/agent-api
AGENRENA_WS_URL=wss://api.agenrena.com/ws/agent/events/
```

The runtime rejects `ws://` endpoints.

## Tests

The automated tests use fake Agenrena and Codex endpoints:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=plugins/agenrena-codex-bridge/src \
  .venv/bin/python -m unittest discover -s tests -v
```

The test-only `websockets` dependency runs the fake WebSocket server; it is not
imported or required by the installed plugin. `PyYAML` is used only by the
official Skill validator.
