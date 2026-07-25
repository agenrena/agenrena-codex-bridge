from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenrena_codex_bridge.models import PendingReply
from agenrena_codex_bridge.state import StateStore


class StateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_is_durable_before_reply_is_marked_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge-state.json"
            store = StateStore(path)
            await store.load()
            reply = PendingReply(
                inbound_message_id="message-1",
                conversation_id="conversation-1",
                thread_id="thread-1",
                turn_id="turn-1",
                text="hello",
            )
            await store.record_codex_result(reply)

            reloaded = StateStore(path)
            await reloaded.load()
            self.assertEqual(
                await reloaded.thread_id_for("conversation-1"), "thread-1"
            )
            self.assertEqual(
                await reloaded.pending_reply_for("message-1"), reply
            )
            self.assertFalse(await reloaded.is_completed("message-1"))

            await reloaded.mark_reply_sent("message-1")
            self.assertIsNone(await reloaded.pending_reply_for("message-1"))
            self.assertTrue(await reloaded.is_completed("message-1"))


if __name__ == "__main__":
    unittest.main()

