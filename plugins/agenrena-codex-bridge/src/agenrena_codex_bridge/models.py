from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class IncomingMedia:
    kind: str
    url: str
    mime_type: Optional[str]


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    conversation_id: str
    sender_id: Optional[str]
    message_type: str
    text: str
    media: tuple[IncomingMedia, ...]
    created_at: Optional[str]

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any]
    ) -> Optional["IncomingMessage"]:
        message_type = str(payload.get("message_type") or "").strip()
        if message_type not in {"text", "image", "sticker"}:
            return None

        message_id = str(payload.get("id") or "").strip()
        conversation_id = str(payload.get("conversation_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not message_id or not conversation_id:
            return None

        media: list[IncomingMedia] = []
        images = payload.get("images")
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, Mapping):
                    continue
                url = str(image.get("url") or "").strip()
                if not url:
                    continue
                mime_type = str(image.get("mime_type") or "").strip() or None
                media.append(
                    IncomingMedia(
                        kind="image",
                        url=url,
                        mime_type=mime_type,
                    )
                )

        if message_type == "sticker":
            sticker = payload.get("sticker")
            if isinstance(sticker, Mapping):
                image_url = str(sticker.get("image_url") or "").strip()
                if image_url:
                    media.append(
                        IncomingMedia(
                            kind="sticker",
                            url=image_url,
                            mime_type="image/png",
                        )
                    )

        if not text and not media:
            return None

        sender = payload.get("sender")
        if not isinstance(sender, Mapping):
            sender = {}

        sender_id = str(sender.get("id") or "").strip() or None
        created_at = str(payload.get("created_at") or "").strip() or None
        return cls(
            message_id=message_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            message_type=message_type,
            text=text,
            media=tuple(media),
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
