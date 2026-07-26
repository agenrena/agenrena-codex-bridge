from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional

from agenrena_codex_bridge.bridge import BridgeService
from agenrena_codex_bridge.media import MaterializedBatch, MaterializedMedia
from agenrena_codex_bridge.models import (
    CodexTurnResult,
    IncomingMessage,
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
        self.media_inputs: list[list[str]] = []
        self.sender_inputs: list[tuple[Optional[str], Optional[str]]] = []

    async def run_turn(
        self,
        *,
        message: IncomingMessage,
        thread_id: Optional[str],
        media=(),
    ) -> CodexTurnResult:
        self.thread_inputs.append(thread_id)
        self.media_inputs.append([item.kind for item in media])
        self.sender_inputs.append((message.sender_id, message.sender_name))
        return CodexTurnResult(
            thread_id=thread_id or "thread-created",
            turn_id=f"turn-{message.message_id}",
            reply_text=f"reply to {message.text}",
        )


class FailingCodexRunner(FakeCodexRunner):
    async def run_turn(
        self,
        *,
        message: IncomingMessage,
        thread_id: Optional[str],
        media=(),
    ) -> CodexTurnResult:
        await super().run_turn(
            message=message,
            thread_id=thread_id,
            media=media,
        )
        raise RuntimeError("fake Codex failure")


def payload(
    message_id: str,
    text: str,
    *,
    sender_id: str = "user-1",
    sender_name: str = "Alice",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "conversation_id": "conversation-1",
        "message_type": "text",
        "sender": {
            "type": "user",
            "id": sender_id,
            "display_name": sender_name,
        },
        "text": text,
        "created_at": "2026-07-24T10:00:00Z",
    }


class FakeMediaStore:
    def __init__(self, root: Path):
        self.root = root
        self.batches: list[MaterializedBatch] = []

    async def materialize(self, sources):
        directory = Path(
            tempfile.mkdtemp(prefix="prepared-", dir=self.root)
        )
        items = []
        for index, source in enumerate(sources):
            path = directory / f"{index + 1}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            items.append(
                MaterializedMedia(
                    kind=source.kind,
                    path=path,
                    mime_type="image/png",
                    size_bytes=12,
                )
            )
        batch = MaterializedBatch(directory=directory, items=tuple(items))
        self.batches.append(batch)
        return batch


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

    async def test_same_conversation_preserves_each_messages_sender(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            source = FakeMessageSource(
                [
                    payload(
                        "message-alice",
                        "from Alice",
                        sender_id="user-alice",
                        sender_name="Alice",
                    ),
                    payload(
                        "message-bob",
                        "from Bob",
                        sender_id="user-bob",
                        sender_name="Bob",
                    ),
                ]
            )
            codex = FakeCodexRunner()
            service = BridgeService(
                message_source=source,
                reply_client=FakeReplyClient(),
                codex_runner=codex,
                state_store=state,
            )

            await service.run()

            self.assertEqual(
                codex.sender_inputs,
                [
                    ("user-alice", "Alice"),
                    ("user-bob", "Bob"),
                ],
            )
            self.assertEqual(codex.thread_inputs, [None, "thread-created"])

    async def test_image_and_sticker_are_materialized_for_codex_and_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / "state.json")
            source = FakeMessageSource(
                [
                    {
                        "id": "image-1",
                        "conversation_id": "conversation-1",
                        "message_type": "image",
                        "text": "What is this?",
                        "images": [
                            {
                                "url": "https://cdn.example/image.png",
                                "mime_type": "image/png",
                            }
                        ],
                    },
                    {
                        "id": "sticker-1",
                        "conversation_id": "conversation-1",
                        "message_type": "sticker",
                        "text": "",
                        "sticker": {
                            "image_url": "https://stickers.example/happy.png",
                        },
                    },
                ]
            )
            replies = FakeReplyClient()
            codex = FakeCodexRunner()
            media_store = FakeMediaStore(root)
            service = BridgeService(
                message_source=source,
                reply_client=replies,
                codex_runner=codex,
                state_store=state,
                media_store=media_store,
            )

            await service.run()

            self.assertEqual(codex.media_inputs, [["image"], ["sticker"]])
            self.assertEqual(len(replies.replies), 2)
            self.assertTrue(
                all(not batch.directory.exists() for batch in media_store.batches)
            )

    async def test_materialized_media_is_cleaned_when_codex_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / "state.json")
            source = FakeMessageSource(
                [
                    {
                        "id": "image-failure",
                        "conversation_id": "conversation-1",
                        "message_type": "image",
                        "text": "",
                        "images": [
                            {"url": "https://cdn.example/image.png"}
                        ],
                    }
                ]
            )
            replies = FakeReplyClient()
            media_store = FakeMediaStore(root)
            service = BridgeService(
                message_source=source,
                reply_client=replies,
                codex_runner=FailingCodexRunner(),
                state_store=state,
                media_store=media_store,
            )

            with self.assertLogs(
                "agenrena_codex_bridge.bridge",
                level="ERROR",
            ):
                await service.run()

            self.assertEqual(replies.replies, [])
            self.assertTrue(
                all(not batch.directory.exists() for batch in media_store.batches)
            )


if __name__ == "__main__":
    unittest.main()
