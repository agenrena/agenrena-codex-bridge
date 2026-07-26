from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Mapping, Optional, Protocol, Sequence

from .codex import CodexRunner
from .media import MaterializedBatch, MaterializedMedia, MediaStore
from .models import IncomingMessage, PendingReply
from .state import StateStore


LOGGER = logging.getLogger(__name__)


class MessageSource(Protocol):
    def messages(self) -> AsyncIterator[Mapping[str, Any]]:
        ...


class ReplyClient(Protocol):
    async def send_reply(self, reply: PendingReply) -> Mapping[str, Any]:
        ...


class TurnRunner(Protocol):
    async def run_turn(
        self,
        *,
        message: IncomingMessage,
        thread_id: Optional[str],
        media: Sequence[MaterializedMedia] = (),
    ):
        ...


class BridgeService:
    def __init__(
        self,
        *,
        message_source: MessageSource,
        reply_client: ReplyClient,
        codex_runner: CodexRunner,
        state_store: StateStore,
        media_store: Optional[MediaStore] = None,
    ):
        self.message_source = message_source
        self.reply_client = reply_client
        self.codex_runner = codex_runner
        self.state_store = state_store
        self.media_store = media_store
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._inflight_message_ids: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        await self.state_store.load()
        await self._flush_pending_replies()

        try:
            async for payload in self.message_source.messages():
                message = IncomingMessage.from_payload(payload)
                if message is None:
                    message_type = str(payload.get("message_type") or "unknown")
                    LOGGER.info(
                        "Ignored unsupported or invalid Agenrena message type=%s.",
                        message_type,
                    )
                    continue

                if message.message_id in self._inflight_message_ids:
                    LOGGER.info(
                        "Ignored duplicate in-flight Agenrena message %s.",
                        message.message_id,
                    )
                    continue
                if await self.state_store.is_completed(message.message_id):
                    LOGGER.info(
                        "Ignored completed Agenrena message %s.",
                        message.message_id,
                    )
                    continue

                self._inflight_message_ids.add(message.message_id)
                task = asyncio.create_task(self._handle_message(message))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except asyncio.CancelledError:
            for task in self._tasks:
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            raise
        finally:
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _handle_message(self, message: IncomingMessage) -> None:
        lock = self._conversation_locks.setdefault(
            message.conversation_id, asyncio.Lock()
        )
        media_batch: Optional[MaterializedBatch] = None
        try:
            if message.media:
                if self.media_store is None:
                    raise RuntimeError(
                        "A media store is required for image and sticker messages."
                    )
                media_batch = await self.media_store.materialize(message.media)

            async with lock:
                if await self.state_store.is_completed(message.message_id):
                    return

                pending = await self.state_store.pending_reply_for(message.message_id)
                if pending is None:
                    thread_id = await self.state_store.thread_id_for(
                        message.conversation_id
                    )
                    result = await self.codex_runner.run_turn(
                        message=message,
                        thread_id=thread_id,
                        media=media_batch.items if media_batch else (),
                    )
                    pending = PendingReply(
                        inbound_message_id=message.message_id,
                        conversation_id=message.conversation_id,
                        thread_id=result.thread_id,
                        turn_id=result.turn_id,
                        text=result.reply_text,
                    )
                    await self.state_store.record_codex_result(pending)

                await self.reply_client.send_reply(pending)
                await self.state_store.mark_reply_sent(message.message_id)
                LOGGER.info(
                    "Replied to Agenrena conversation %s for message %s.",
                    message.conversation_id,
                    message.message_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Failed to process Agenrena message %s in conversation %s.",
                message.message_id,
                message.conversation_id,
            )
        finally:
            if media_batch is not None:
                await media_batch.cleanup()
            self._inflight_message_ids.discard(message.message_id)

    async def _flush_pending_replies(self) -> None:
        replies = await self.state_store.list_pending_replies()
        for reply in replies:
            try:
                await self.reply_client.send_reply(reply)
                await self.state_store.mark_reply_sent(reply.inbound_message_id)
                LOGGER.info(
                    "Delivered pending reply for Agenrena message %s.",
                    reply.inbound_message_id,
                )
            except Exception:
                LOGGER.exception(
                    "Could not deliver pending reply for Agenrena message %s; "
                    "it remains in bridge state.",
                    reply.inbound_message_id,
                )
