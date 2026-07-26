from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from agenrena_codex_bridge.codex import CodexRunner
from agenrena_codex_bridge.media import MaterializedMedia
from agenrena_codex_bridge.models import IncomingMedia, IncomingMessage


class CodexRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_app_server_starts_and_resumes_native_thread(self):
        test_dir = Path(__file__).parent
        runner = CodexRunner(
            codex_bin="unused",
            workspace=test_dir,
            timeout_seconds=5,
            command_override=[
                sys.executable,
                "-u",
                str(test_dir / "fake_codex_app_server.py"),
            ],
        )
        message = IncomingMessage(
            message_id="message-1",
            conversation_id="conversation-1",
            sender_id="user-1",
            message_type="text",
            text="hello",
            media=(),
            created_at=None,
        )

        first = await runner.run_turn(message=message, thread_id=None)
        second = await runner.run_turn(message=message, thread_id=first.thread_id)

        self.assertEqual(first.thread_id, "thread-new")
        self.assertEqual(second.thread_id, "thread-new")
        self.assertEqual(first.reply_text, "Fake Codex reply")
        self.assertEqual(first.turn_id, "turn-test")

    async def test_turn_sends_text_images_and_sticker_as_local_image_inputs(self):
        test_dir = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture_path = root / "turn.json"
            image_path = root / "photo.jpg"
            sticker_path = root / "sticker.png"
            image_path.write_bytes(b"\xff\xd8\xfffake")
            sticker_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            runner = CodexRunner(
                codex_bin="unused",
                workspace=test_dir,
                timeout_seconds=5,
                command_override=[
                    sys.executable,
                    "-u",
                    str(test_dir / "fake_codex_app_server.py"),
                    str(capture_path),
                ],
            )
            message = IncomingMessage(
                message_id="message-media",
                conversation_id="conversation-1",
                sender_id="user-1",
                message_type="image",
                text="Please explain these.",
                media=(
                    IncomingMedia(
                        kind="image",
                        url="https://cdn.example/photo.jpg",
                        mime_type="image/jpeg",
                    ),
                    IncomingMedia(
                        kind="sticker",
                        url="https://stickers.example/sticker.png",
                        mime_type="image/png",
                    ),
                ),
                created_at=None,
            )
            materialized = (
                MaterializedMedia(
                    kind="image",
                    path=image_path,
                    mime_type="image/jpeg",
                    size_bytes=image_path.stat().st_size,
                ),
                MaterializedMedia(
                    kind="sticker",
                    path=sticker_path,
                    mime_type="image/png",
                    size_bytes=sticker_path.stat().st_size,
                ),
            )

            await runner.run_turn(
                message=message,
                thread_id=None,
                media=materialized,
            )

            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(
                captured["input"],
                [
                    {
                        "type": "text",
                        "text": 'Agenrena sender: {"id":"user-1"}',
                        "text_elements": [],
                    },
                    {
                        "type": "text",
                        "text": "Please explain these.",
                        "text_elements": [],
                    },
                    {
                        "type": "localImage",
                        "path": str(image_path),
                    },
                    {
                        "type": "text",
                        "text": "The user sent the following sticker.",
                        "text_elements": [],
                    },
                    {
                        "type": "localImage",
                        "path": str(sticker_path),
                    },
                ],
            )

    async def test_sender_metadata_is_json_encoded_for_media_only_turn(self):
        test_dir = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture_path = root / "turn.json"
            sticker_path = root / "sticker.png"
            sticker_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            runner = CodexRunner(
                codex_bin="unused",
                workspace=test_dir,
                timeout_seconds=5,
                command_override=[
                    sys.executable,
                    "-u",
                    str(test_dir / "fake_codex_app_server.py"),
                    str(capture_path),
                ],
            )
            message = IncomingMessage(
                message_id="message-sticker",
                conversation_id="conversation-1",
                sender_id='user-"1\n',
                message_type="sticker",
                text="",
                media=(
                    IncomingMedia(
                        kind="sticker",
                        url="https://stickers.example/sticker.png",
                        mime_type="image/png",
                    ),
                ),
                created_at=None,
            )
            materialized = (
                MaterializedMedia(
                    kind="sticker",
                    path=sticker_path,
                    mime_type="image/png",
                    size_bytes=sticker_path.stat().st_size,
                ),
            )

            await runner.run_turn(
                message=message,
                thread_id=None,
                media=materialized,
            )

            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(
                captured["input"][0],
                {
                    "type": "text",
                    "text": 'Agenrena sender: {"id":"user-\\"1\\n"}',
                    "text_elements": [],
                },
            )
            self.assertEqual(
                [item["type"] for item in captured["input"]],
                ["text", "text", "localImage"],
            )

    async def test_turn_without_sender_does_not_add_metadata(self):
        test_dir = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "turn.json"
            runner = CodexRunner(
                codex_bin="unused",
                workspace=test_dir,
                timeout_seconds=5,
                command_override=[
                    sys.executable,
                    "-u",
                    str(test_dir / "fake_codex_app_server.py"),
                    str(capture_path),
                ],
            )
            message = IncomingMessage(
                message_id="message-anonymous",
                conversation_id="conversation-1",
                sender_id=None,
                message_type="text",
                text="hello",
                media=(),
                created_at=None,
            )

            await runner.run_turn(message=message, thread_id=None)

            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(
                captured["input"],
                [
                    {
                        "type": "text",
                        "text": "hello",
                        "text_elements": [],
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
