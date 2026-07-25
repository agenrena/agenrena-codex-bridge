from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from .models import PendingReply


STATE_VERSION = 1
MAX_COMPLETED_MESSAGE_IDS = 5000


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "sessions": {},
        "pending_replies": {},
        "completed_message_ids": [],
    }


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._data = _default_state()
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        async with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._data = _default_state()
                return

            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise RuntimeError(f"Bridge state at {self.path} must be a JSON object.")

            self._data = {
                **_default_state(),
                **parsed,
                "sessions": dict(parsed.get("sessions") or {}),
                "pending_replies": dict(parsed.get("pending_replies") or {}),
                "completed_message_ids": list(parsed.get("completed_message_ids") or []),
            }

    async def thread_id_for(self, conversation_id: str) -> Optional[str]:
        async with self._lock:
            value = self._data["sessions"].get(conversation_id)
            return str(value) if value else None

    async def is_completed(self, message_id: str) -> bool:
        async with self._lock:
            return message_id in self._data["completed_message_ids"]

    async def pending_reply_for(self, message_id: str) -> Optional[PendingReply]:
        async with self._lock:
            value = self._data["pending_replies"].get(message_id)
            return PendingReply.from_dict(value) if value else None

    async def list_pending_replies(self) -> list[PendingReply]:
        async with self._lock:
            return [
                PendingReply.from_dict(value)
                for value in self._data["pending_replies"].values()
            ]

    async def record_codex_result(self, reply: PendingReply) -> None:
        async with self._lock:
            self._data["sessions"][reply.conversation_id] = reply.thread_id
            self._data["pending_replies"][reply.inbound_message_id] = reply.to_dict()
            self._save_locked()

    async def mark_reply_sent(self, message_id: str) -> None:
        async with self._lock:
            self._data["pending_replies"].pop(message_id, None)
            completed = [
                value
                for value in self._data["completed_message_ids"]
                if value != message_id
            ]
            completed.append(message_id)
            self._data["completed_message_ids"] = completed[-MAX_COMPLETED_MESSAGE_IDS:]
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.tmp"
        )
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)
