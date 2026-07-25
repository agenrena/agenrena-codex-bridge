from __future__ import annotations

import sys
import unittest
from pathlib import Path

from agenrena_codex_bridge.codex import CodexRunner
from agenrena_codex_bridge.models import IncomingTextMessage


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
        message = IncomingTextMessage(
            message_id="message-1",
            conversation_id="conversation-1",
            sender_id="user-1",
            sender_name="Alice",
            text="hello",
            created_at=None,
        )

        first = await runner.run_turn(message=message, thread_id=None)
        second = await runner.run_turn(message=message, thread_id=first.thread_id)

        self.assertEqual(first.thread_id, "thread-new")
        self.assertEqual(second.thread_id, "thread-new")
        self.assertEqual(first.reply_text, "Fake Codex reply")
        self.assertEqual(first.turn_id, "turn-test")


if __name__ == "__main__":
    unittest.main()

