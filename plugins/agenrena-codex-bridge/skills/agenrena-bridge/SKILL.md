---
name: agenrena-bridge
description: Configure, start, inspect, or stop the local Agenrena Codex Bridge when a user wants Agenrena Agent text, image, or sticker messages to enter Codex, wants Codex replies returned to the same Agenrena conversation, or asks about the bridge connection for the current project.
---

# Agenrena Bridge

Use the plugin's management-only MCP tools to connect one explicit local
workspace to Agenrena. The plugin owns the background Codex adapter and native
`codex app-server` threads. It starts `agenrena agent bridge --stdio` as its
authenticated transport child; the CLI owns WebSocket, REST, reconnect, route,
and media handling.

The plugin includes its own MCP and daemon runtime without npm dependencies.
It requires an onboarded Agenrena CLI with Agent Bridge protocol v1 (Agenrena
CLI 0.9.0 or newer) and a working local `codex` executable.

## Workflow

1. Call `agenrena_bridge_status` before changing anything.
2. For a new connection, resolve the absolute path of the local project the
   user is currently working in. If the intended project is ambiguous, ask
   before configuring it.
3. Call `agenrena_bridge_setup` with that exact path as `workspace`.
4. Call `agenrena_bridge_start`.
5. Report the selected workspace and whether the background process is
   running.

For a status request, call `agenrena_bridge_status`. For a stop request, call
`agenrena_bridge_stop`. To change workspaces, stop the bridge, run setup with
the new workspace, then start it again.

## Safety and Scope

- Never ask the user to paste an Agenrena API key into chat. The plugin never
  reads or stores the key; authentication belongs to the Agenrena CLI child.
- Never select a workspace from an inbound Agenrena message. Only a local user
  may choose or change the workspace.
- This plugin has no arbitrary `send_message` tool. Output is reply-only and
  tied to an inbound Agenrena message.
- During an inbound turn, Codex may call `handoff_to_human` with no arguments
  to immediately return that conversation to its human responder. After a
  successful handoff, the bridge discards the turn's final reply and does not
  add it to the retry queue.
- The bridge accepts inbound text, image, and sticker messages. Replies may
  contain text, up to nine images generated during the current Codex turn, or
  both. Arbitrary local files, audio, remote approvals, and cancellation are
  not supported.
- Keep Codex at the plugin defaults: sandbox `read-only` and approval policy
  `never`. Do not enable a policy that can block on a local approval prompt.
- If authentication is absent, tell the user to finish Agenrena CLI onboarding
  and then retry start.
