from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class IncomingTextMessage:
    message_id: str
    conversation_id: str
    sender_id: Optional[str]
    sender_name: Optional[str]
    text: str
    created_at: Optional[str]

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any]
    ) -> Optional["IncomingTextMessage"]:
        if payload.get("message_type") != "text":
            return None

        message_id = str(payload.get("id") or "").strip()
        conversation_id = str(payload.get("conversation_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not message_id or not conversation_id or not text:
            return None

        sender = payload.get("sender")
        if not isinstance(sender, Mapping):
            sender = {}

        sender_id = str(sender.get("id") or "").strip() or None
        sender_name = str(sender.get("display_name") or "").strip() or None
        created_at = str(payload.get("created_at") or "").strip() or None
        return cls(
            message_id=message_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            created_at=created_at,
        )


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    reply_text: str


@dataclass(frozen=True)
class PendingReply:
    inbound_message_id: str
    conversation_id: str
    thread_id: str
    turn_id: str
    text: str

    @property
    def outbound_message_id(self) -> str:
        value = f"codex-{self.inbound_message_id}"
        return value[:100]

    def to_dict(self) -> dict[str, str]:
        return {
            "inbound_message_id": self.inbound_message_id,
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PendingReply":
        return cls(
            inbound_message_id=str(value["inbound_message_id"]),
            conversation_id=str(value["conversation_id"]),
            thread_id=str(value["thread_id"]),
            turn_id=str(value["turn_id"]),
            text=str(value["text"]),
        )
