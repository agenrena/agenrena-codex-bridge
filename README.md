# Agenrena Codex Bridge

A Codex plugin surface for the native Go bridge in the Agenrena CLI. It
connects normalized Agenrena Agent messages and experimental incoming voice
calls to a local Codex project:

```text
Agenrena WS / REST / media
        ↕
agenrena agent bridge --stdio
        ↕
Agenrena CLI Codex daemon
        ↕
Codex app-server

LiveKit WebRTC ↔ Go RTC helper ↔ Codex WebRTC
```

Inbound messages may contain text, images, or a sticker. An Agenrena message
starts or resumes a native Codex thread, downloaded media is supplied through
Codex `localImage` inputs, and sanitized turn progress is published over the
authenticated Agent WebSocket for the existing User Global SSE. Codex's final
text plus images generated during that turn are still persisted through the
REST message endpoint and sent back to the same conversation. The plugin does
not expose a tool for initiating arbitrary Agenrena messages.

For every inbound turn, the sender's platform-supplied Agenrena ID is provided
to Codex as authenticated transport metadata in developer instructions. It is
refreshed when starting or resuming a thread, is kept separate from user text,
and is `null` when the transport supplies no sender ID. Sender display names
are not forwarded.

See [PLAN.md](PLAN.md) for the protocol contracts and later phases.

## Plugin contents

- `.agents/plugins/marketplace.json`: installable Agenrena marketplace entry.
- `plugins/agenrena-codex-bridge/.codex-plugin/plugin.json`: Codex plugin
  manifest.
- `plugins/agenrena-codex-bridge/skills/agenrena-bridge`: instructions for
  Codex to configure and operate the bridge safely.
- `plugins/agenrena-codex-bridge/.mcp.json`: starts a portable launcher that
  resolves the native CLI and runs `agenrena codex bridge mcp`.
- The Agenrena CLI owns the compiled Go MCP server and daemon as well as
  authenticated transport, Codex app-server, thread continuity, route-keyed
  state, deduplication, pending replies, workspace, sandbox, and approval
  policy.
- The plugin owns the Codex-facing registration, prompts, and safe operating
  instructions.

The MCP server exposes only:

- `agenrena_bridge_setup`
- `agenrena_bridge_start`
- `agenrena_bridge_status`
- `agenrena_bridge_stop`

It deliberately has no `send_message` tool.

## Experimental voice calls

Incoming voice calls use the same native Go implementation exercised by the
staging plugin. The Codex bridge coordinates call signaling and app-server SDP
exchange, while `agenrena-rtc-helper` bridges LiveKit and Codex WebRTC media
directly. Each call starts a fresh Codex thread and does not reuse the text
conversation's thread mapping.

The plugin enables calls with `AGENRENA_CODEX_BRIDGE_CALLS=true`, pins realtime
protocol `v3`, and requests `gpt-live-1-codex`. This remains experimental:
end-to-end reliability has not been fully validated, and Codex realtime account
entitlement may reject call creation.

## Install from GitHub

The `agenrena` (0.12.0 or newer), matching `agenrena-rtc-helper`, and `codex`
executables are required for voice calls. The standard Agenrena installer puts
the CLI and RTC helper together. Users do not need Node.js, npm, Python, Go, a
virtual environment, or other packages. The compiled Agenrena executable
provides both the management MCP and detached daemon.

```bash
curl -fsSL https://raw.githubusercontent.com/agenrena/agenrena-cli/main/install.sh | sh
```

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

Codex will save the current project's absolute directory as plugin-only
configuration and start the background bridge. Agenrena credentials stay
entirely inside the CLI.

## Credentials

The plugin never opens the Agenrena credential file. `agenrena agent bridge
--stdio` loads the credentials established during CLI onboarding and returns a
sanitized authentication error if onboarding is incomplete.

## Local files

The plugin stores non-secret configuration at:

```text
~/.config/agenrena-codex-bridge/config.json
```

Runtime status, logs, Codex thread mappings, deduplication state, temporary
inbound media, and pending outbound generated images are stored at:

```text
~/.local/state/agenrena-codex-bridge/
```

They can be redirected with `AGENRENA_CODEX_BRIDGE_CONFIG_FILE`,
`AGENRENA_CODEX_BRIDGE_STATE_DIR`, `XDG_CONFIG_HOME`, or `XDG_STATE_HOME`.
Inbound media validation and retention are owned by the generic CLI bridge.
The native Codex bridge stages generated outbound images here until delivery
is confirmed, then removes them.

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
```

Normal plugin setup saves the selected workspace in the bridge config, so it
does not need to be exported on every launch.

## Tests

The bridge runtime and RTC helper are tested in the Agenrena CLI Go repository:

```bash
go test ./internal/codexbridge ./...
cd rtc-helper
go test -tags nolibopusfile ./...
```
