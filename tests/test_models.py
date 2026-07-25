from __future__ import annotations

import unittest

from agenrena_codex_bridge.models import IncomingTextMessage, PendingReply


class ModelTests(unittest.TestCase):
    def test_current_agenrena_text_payload_is_normalized(self):
        message = IncomingTextMessage.from_payload(
            {
                "id": "message-1",
                "conversation_id": "conversation-1",
                "message_type": "text",
                "sender": {
                    "type": "user",
                    "id": "user-1",
                    "display_name": "Alice",
                },
                "text": "  hello Codex  ",
                "created_at": "2026-07-24T10:00:00Z",
                "images": [],
            }
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "hello Codex")
        self.assertEqual(message.sender_name, "Alice")

    def test_non_text_and_empty_text_are_ignored(self):
        self.assertIsNone(
            IncomingTextMessage.from_payload(
                {
                    "id": "image-1",
                    "conversation_id": "conversation-1",
                    "message_type": "image",
                    "text": "",
                }
            )
        )
        self.assertIsNone(
            IncomingTextMessage.from_payload(
                {
                    "id": "message-1",
                    "conversation_id": "conversation-1",
                    "message_type": "text",
                    "text": " ",
                }
            )
        )

    def test_outbound_message_id_is_stable_and_bounded(self):
        reply = PendingReply(
            inbound_message_id="x" * 200,
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            text="reply",
        )
        self.assertEqual(reply.outbound_message_id, reply.outbound_message_id)
        self.assertLessEqual(len(reply.outbound_message_id), 100)


if __name__ == "__main__":
    unittest.main()

