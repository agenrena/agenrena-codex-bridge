# Agenrena Codex Bridge

A Codex plugin-owned adapter that connects normalized Agenrena Agent messages
to a local Codex project:

```text
Agenrena WS / REST / media
        ↕
agenrena agent bridge --stdio
        ↕
plugin-owned daemon
        ↕
Codex app-server
```

Inbound messages may contain text, images, or a sticker. An Agenrena message
starts or resumes a native Codex thread, downloaded media is supplied through
Codex `localImage` inputs, and Codex's final text plus images generated during
that turn are sent back to the same conversation. The plugin does not expose a
tool for initiating arbitrary Agenrena messages.

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
- `plugins/agenrena-codex-bridge/.mcp.json`: local management MCP server
  registration.
- `plugins/agenrena-codex-bridge/skills/agenrena-bridge`: instructions for
  Codex to configure and operate the bridge safely.
- `plugins/agenrena-codex-bridge/.mcp.json`: starts the plugin's management MCP.
- `plugins/agenrena-codex-bridge/runtime`: dependency-free Node MCP, supervisor,
  daemon, generic bridge client, Codex app-server client, and durable state.
- The Agenrena CLI owns only authenticated transport: WebSocket, REST, media,
  route generation, retry, and reconnect.
- The plugin owns Codex app-server, thread continuity, route-keyed state,
  deduplication, pending replies, workspace, sandbox, and approval policy.

The MCP server exposes only:

- `agenrena_bridge_setup`
- `agenrena_bridge_start`
- `agenrena_bridge_status`
- `agenrena_bridge_stop`

It deliberately has no `send_message` tool.

## Install from GitHub

The `agenrena` (0.9.0 or newer) and `codex` executables are required. Users do
not need Python, Go, a virtual environment, npm, or other packages. The plugin
reuses the Node runtime supplied to local Codex plugins and spawns its daemon
with that same executable.

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
The plugin stages generated outbound images here until the CLI confirms the
reply was sent, then removes them.

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

Normal plugin setup saves the selected workspace in the bridge config, so it
does not need to be exported on every launch.

## Tests

The plugin runtime tests use only Node's built-in test runner:

```bash
node --test plugins/agenrena-codex-bridge/runtime/runtime.test.mjs
```
