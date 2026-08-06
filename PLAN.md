# Agenrena Codex Bridge 1.0

## Ownership

The integration has one generic transport and one thin Codex adapter:

```text
Agenrena
  ↕ WebSocket / REST / media
agenrena agent bridge --stdio
  ↕ JSON-RPC 2.0 over JSON Lines
plugin-owned Codex daemon
  ↕ JSON-RPC over JSON Lines
codex app-server
```

The Agenrena CLI owns authentication, Agent API and WebSocket compatibility,
reconnect, normalized inbound messages, opaque routes, media materialization,
outbound delivery, and transport retries. It does not know how to run Codex.

This plugin owns MCP lifecycle tools, its detached daemon, Codex workspace and
safety policy, app-server process management, thread continuity, route-keyed
state, inbound deduplication, pending reply recovery, and conversion between
normalized Agenrena messages and Codex inputs.

## Plugin control plane

The MCP server is management-only:

- `agenrena_bridge_setup`
- `agenrena_bridge_start`
- `agenrena_bridge_status`
- `agenrena_bridge_stop`

It never carries chat messages. `start` launches a detached plugin daemon, so
an MCP reload does not disconnect the active Agenrena bridge.

## Data plane

The daemon starts exactly one `agenrena agent bridge --stdio` child and sends
Agent Bridge protocol v1 `initialize` with agent type `codex`. It consumes
`messages/received`, treats `route` as opaque, and sends final answers with
`messages/send` using stable `codex-<inbound-id>` client message IDs.

For every inbound message, the daemon starts a local `codex app-server`, starts
or resumes the thread stored for that opaque route, sends text and `localImage`
inputs, waits for the final agent message, and closes app-server. Turns for one
route are serialized; different routes may run concurrently.

## Configuration and state

Plugin configuration stores only the selected workspace. It never reads or
stores Agenrena credentials or endpoints.

```text
~/.config/agenrena-codex-bridge/config.json
~/.local/state/agenrena-codex-bridge/process.json
~/.local/state/agenrena-codex-bridge/state.json
~/.local/state/agenrena-codex-bridge/bridge.log
```

The defaults are Codex sandbox `read-only`, approval policy `never`, and a
15-minute turn timeout. The plugin declines app-server approval requests
because no remote approval UX exists.

## Compatibility

Version 1.0 is intentionally destructive. It requires Agenrena CLI 0.9.0 or
newer and Agent Bridge protocol v1. It does not migrate the previous CLI-owned
`codex-bridge` config, process, conversation state, or pending replies. Users
reinstall the plugin and configure its workspace again.
