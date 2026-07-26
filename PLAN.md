# Agenrena Codex Bridge Plan

## Goal

Build a Codex plugin whose background bridge receives text, image, and sticker
messages over Agenrena's Agent WebSocket, runs each message through a native
Codex app-server thread, and posts the final text reply back to the same
Agenrena conversation through the Agent HTTP API.

The bridge is a reply-only integration. It will not expose a Codex tool that
can initiate arbitrary Agenrena messages.

The plugin's MCP surface is management-only: setup, start, status, and stop.

## Phase 1: Inbound Messages

### In scope

- Load the Agenrena API key from the existing Agenrena CLI credentials file.
- Connect as a WebSocket client to Agenrena Agent events.
- Use a bundled standard-library RFC 6455 client so GitHub plugin installation
  requires no pip or virtual environment bootstrap.
- Accept Agenrena `message_type: "text"`, `"image"`, and `"sticker"` payloads.
- Download `images[].url` and `sticker.image_url` into restricted temporary
  storage using only the Python standard library.
- Require public HTTPS media targets, validate every redirect and resolved
  address, enforce count and byte limits, and verify image signatures.
- Deduplicate inbound Agenrena message IDs.
- Map each Agenrena `conversation_id` to one persistent Codex `threadId`.
- Start or resume a Codex thread through `codex app-server`.
- Send inbound text plus absolute-path `localImage` items with `turn/start`.
- Remove materialized media after the Codex turn and stale media after an
  abnormal shutdown.
- Collect the Codex `final_answer`.
- Reply through `POST /api/agent-api/channels/messages/send/`.
- Reconnect the Agenrena WebSocket with bounded exponential backoff.
- Serialize turns within one conversation while allowing different
  conversations to run independently.
- Persist session mappings and completed message IDs using atomic JSON writes.
- Provide unit and protocol-level tests using fake Agenrena and Codex clients.

### Out of scope

- Audio or file input.
- Voice or image replies.
- Codex-initiated messages to arbitrary Agenrena conversations.
- Arbitrary or proactive outbound messaging tools.
- Remote approval handling.
- Multiple project selection from a single conversation.
- Streaming partial Codex responses to Agenrena.
- Replay or history recovery for messages that arrived while the WebSocket was
  disconnected.

## Transport Contracts

### Agenrena inbound WebSocket

Endpoint:

```text
wss://<agenrena-host>/ws/agent/events/?token=<URL-encoded-api-key>
```

Runtime configuration rejects unencrypted `ws://` endpoints. Protocol tests
may use loopback `ws://` servers only to exercise framing without external
network access.

Phase 1 consumes the current Agenrena message payload directly. Text messages
use:

```json
{
  "id": "message-uuid",
  "conversation_id": "conversation-uuid",
  "message_type": "text",
  "sender": {
    "type": "user",
    "id": "user-uuid",
    "display_name": "Alice"
  },
  "text": "Hello",
  "created_at": "2026-03-24T08:00:00.000Z"
}
```

Image messages additionally carry one to nine entries in `images[]`, each with
an HTTPS `url` and optional `mime_type`. Sticker messages carry an HTTPS image
at `sticker.image_url`. Events without a non-empty `id`,
`conversation_id`, or any supported text/media content are ignored.

### Codex app-server

The bridge starts a local `codex app-server` subprocess for a turn and speaks
newline-delimited JSON-RPC over stdin/stdout:

```text
initialize
thread/start | thread/resume
turn/start
```

Phase 1 sends text when present:

```json
{
  "type": "text",
  "text": "Hello",
  "text_elements": []
}
```

When `sender.id` is present, the bridge first sends a separate text input
containing compact JSON:

```text
Agenrena sender: {"id":"user-123"}
```

The ID is JSON-encoded and becomes part of the Codex thread history. Sender
display names are ignored, and a missing sender ID does not produce a metadata
item.

Downloaded images and stickers are supplied as:

```json
{
  "type": "localImage",
  "path": "/absolute/path/to/materialized-image.png"
}
```

Sticker images are preceded by a short application-generated text item that
identifies the input as a sticker.

The bridge collects `agentMessage` items and resolves only after
`turn/completed`. It prefers the item whose phase is `final_answer`.

Phase 1 defaults to `read-only` with approval policy `never`. Approval requests
are declined because there is not yet a remote approval UX.

### Agenrena outbound HTTP

Endpoint relative to `AGENRENA_API_BASE`:

```text
POST /channels/messages/send/
```

Body:

```json
{
  "source": "agenrena",
  "conversation_id": "conversation-uuid",
  "text": "Codex reply",
  "message_id": "codex-<stable-inbound-message-id>",
  "reply_to_message_id": "inbound-message-uuid"
}
```

Authentication:

```text
Authorization: Bearer <api-key>
```

The stable `message_id` makes retries idempotent against Agenrena's existing
client-message deduplication.

## Credentials and Configuration

The bridge is compatible with the Agenrena CLI credential layout.

Credential lookup order:

1. `AGENRENA_CREDENTIALS_FILE`, when explicitly set.
2. `$AGENRENA_CONFIG_DIR/credentials.json`.
3. `$XDG_CONFIG_HOME/agenrena/credentials.json`.
4. `~/.config/agenrena/credentials.json`.

Expected file:

```json
{
  "version": 1,
  "auth_type": "api_key",
  "api_key": "agr_..."
}
```

The API key must never be written to logs or bridge state.

Runtime environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENRENA_API_BASE` | `https://api.agenrena.com/api/agent-api` | Agent REST API base |
| `AGENRENA_WS_URL` | `wss://api.agenrena.com/ws/agent/events/` | TLS Agent WebSocket endpoint |
| `AGENRENA_CONFIG_DIR` | Agenrena CLI default | Credential directory |
| `AGENRENA_CREDENTIALS_FILE` | unset | Exact credential file override |
| `CODEX_BIN` | `codex` | Codex executable |
| `CODEX_WORKSPACE` | current directory | Codex thread working directory |
| `CODEX_MODEL` | unset | Optional model override |
| `CODEX_SANDBOX_MODE` | `read-only` | Codex sandbox |
| `CODEX_APPROVAL_POLICY` | `never` | Codex approval policy |
| `AGENRENA_BRIDGE_CONFIG_FILE` | `~/.config/agenrena-codex-bridge/config.json` | Non-secret plugin configuration |
| `BRIDGE_STATE_DIR` | `~/.local/state/agenrena-codex-bridge` | Process, session, log, and dedupe state |
| `LOG_LEVEL` | `INFO` | Bridge logging level |

## Reliability Rules

- Never log credentials or a WebSocket URL containing the token.
- Never log signed media URL queries.
- Reject media that resolves to non-public addresses, exceeds limits, or does
  not have a supported JPEG, PNG, GIF, or WebP signature.
- Keep at most one active turn per Agenrena conversation.
- Do not mark an inbound message complete until the Agenrena reply API succeeds.
- Use a stable outbound client message ID for retries.
- Retry temporary HTTP failures and `429`, respecting `Retry-After` when present.
- Reconnect WebSocket failures with jittered exponential backoff capped at
  30 seconds.
- Shut down active Codex subprocesses on bridge termination.
- Persist state with write-to-temp plus atomic rename.

## Phase 1 Acceptance Criteria

- A valid Agenrena text, image, or sticker WebSocket payload starts a Codex
  turn.
- An available sender ID reaches Codex as short, JSON-encoded metadata before
  the user content; sender display names are not forwarded.
- Image and sticker media reaches Codex through `localImage` input and is
  removed after the turn.
- The first message creates and stores a Codex thread ID.
- A later message in the same conversation resumes that thread.
- The Codex final answer is posted to the same Agenrena conversation.
- Duplicate inbound message IDs do not produce duplicate Codex turns or replies.
- Messages from two different conversations can run independently.
- Missing or invalid credentials fail before the WebSocket connection starts.
- Unit and fake-protocol tests pass without requiring live Agenrena access.
- The Codex skill and plugin manifests pass their official validators.
- The repository marketplace resolves to the packaged
  `agenrena-codex-bridge` plugin.
- The MCP server and bridge import successfully under system Python without
  third-party packages.

## Later Phases

### Phase 2: Remote control

- Approval requests and decisions.
- Cancellation.
- Run status and optional progress messages.
- Explicit workspace/project routing.

### Phase 3: Delivery recovery

- Agenrena replay cursor or missed-message API.
- Durable job queue.
- Multi-process locking and horizontal scaling.
