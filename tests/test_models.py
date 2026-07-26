from __future__ import annotations

import unittest

from agenrena_codex_bridge.models import IncomingMessage, PendingReply


class ModelTests(unittest.TestCase):
    def test_current_agenrena_text_payload_is_normalized(self):
        message = IncomingMessage.from_payload(
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
        self.assertEqual(message.sender_id, "user-1")
        self.assertFalse(hasattr(message, "sender_name"))
        self.assertEqual(message.message_type, "text")
        self.assertEqual(message.media, ())

    def test_image_only_payload_is_normalized(self):
        message = IncomingMessage.from_payload(
            {
                "id": "image-1",
                "conversation_id": "conversation-1",
                "message_type": "image",
                "text": "",
                "images": [
                    {
                        "url": "https://cdn.example/image.jpg?signature=secret",
                        "mime_type": "image/jpeg",
                    }
                ],
            }
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "")
        self.assertEqual(len(message.media), 1)
        self.assertEqual(message.media[0].kind, "image")
        self.assertEqual(message.media[0].mime_type, "image/jpeg")

    def test_sticker_payload_uses_sticker_image_url(self):
        message = IncomingMessage.from_payload(
            {
                "id": "sticker-message-1",
                "conversation_id": "conversation-1",
                "message_type": "sticker",
                "text": "",
                "images": [],
                "sticker": {
                    "id": "message-sticker-1",
                    "sticker_id": "sticker-1",
                    "pack_id": "pack-1",
                    "pack_available": True,
                    "image_url": "https://stickers.example/sticker.png",
                },
            }
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(len(message.media), 1)
        self.assertEqual(message.media[0].kind, "sticker")
        self.assertEqual(
            message.media[0].url,
            "https://stickers.example/sticker.png",
        )

    def test_unsupported_and_empty_messages_are_ignored(self):
        self.assertIsNone(
            IncomingMessage.from_payload(
                {
                    "id": "message-1",
                    "conversation_id": "conversation-1",
                    "message_type": "text",
                    "text": " ",
                }
            )
        )
        self.assertIsNone(
            IncomingMessage.from_payload(
                {
                    "id": "audio-1",
                    "conversation_id": "conversation-1",
                    "message_type": "audio",
                    "text": "listen",
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
