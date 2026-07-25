from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional

from agenrena_codex_bridge.bridge import BridgeService
from agenrena_codex_bridge.models import (
    CodexTurnResult,
    IncomingTextMessage,
    PendingReply,
)
from agenrena_codex_bridge.state import StateStore


class FakeMessageSource:
    def __init__(self, payloads):
        self.payloads = payloads

    async def messages(self) -> AsyncIterator[Mapping[str, Any]]:
        for payload in self.payloads:
            yield payload


class FakeReplyClient:
    def __init__(self):
        self.replies: list[PendingReply] = []

    async def send_reply(self, reply: PendingReply) -> Mapping[str, Any]:
        self.replies.append(reply)
        return {"message_id": reply.outbound_message_id}


class FakeCodexRunner:
    def __init__(self):
        self.thread_inputs: list[Optional[str]] = []

    async def run_turn(
        self,
        *,
        message: IncomingTextMessage,
        thread_id: Optional[str],
    ) -> CodexTurnResult:
        self.thread_inputs.append(thread_id)
        return CodexTurnResult(
            thread_id=thread_id or "thread-created",
            turn_id=f"turn-{message.message_id}",
            reply_text=f"reply to {message.text}",
        )


def payload(message_id: str, text: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "conversation_id": "conversation-1",
        "message_type": "text",
        "sender": {"type": "user", "id": "user-1", "display_name": "Alice"},
        "text": text,
        "created_at": "2026-07-24T10:00:00Z",
    }


class BridgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_messages_create_then_resume_thread_and_dedupe(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            source = FakeMessageSource(
                [
                    payload("message-1", "first"),
                    payload("message-2", "second"),
                    payload("message-1", "first duplicate"),
                ]
            )
            replies = FakeReplyClient()
            codex = FakeCodexRunner()
            service = BridgeService(
                message_source=source,
                reply_client=replies,
                codex_runner=codex,
                state_store=state,
            )

            await service.run()

            self.assertEqual(codex.thread_inputs, [None, "thread-created"])
            self.assertEqual(len(replies.replies), 2)
            self.assertEqual(
                [reply.inbound_message_id for reply in replies.replies],
                ["message-1", "message-2"],
            )


if __name__ == "__main__":
    unittest.main()
