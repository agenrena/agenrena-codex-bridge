from __future__ import annotations

import asyncio
import json
import logging
import random
from email.message import Message
from typing import Any, AsyncIterator, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .models import PendingReply
from .websocket import WebSocketConnection


LOGGER = logging.getLogger(__name__)


class AgenrenaAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


def _retry_after(headers: Optional[Message]) -> Optional[float]:
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class AgenrenaAPIClient:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        user_agent: str,
        timeout_seconds: float = 60.0,
        max_attempts: int = 4,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    async def send_reply(self, reply: PendingReply) -> Mapping[str, Any]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await asyncio.to_thread(self._send_reply_sync, reply)
            except AgenrenaAPIError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise
                delay = (
                    error.retry_after
                    if error.retry_after is not None
                    else min(2 ** (attempt - 1), 8)
                )
                LOGGER.warning(
                    "Temporary Agenrena reply API failure; retrying in %.1fs "
                    "(attempt %d/%d).",
                    delay,
                    attempt,
                    self.max_attempts,
                )
                await asyncio.sleep(delay)

        raise AssertionError("unreachable")

    def _send_reply_sync(self, reply: PendingReply) -> Mapping[str, Any]:
        endpoint = f"{self.api_base}/channels/messages/send/"
        body = json.dumps(
            {
                "source": "agenrena",
                "conversation_id": reply.conversation_id,
                "text": reply.text,
                "message_id": reply.outbound_message_id,
                "reply_to_message_id": reply.inbound_message_id,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read(4096).decode("utf-8", errors="replace").strip()
            raise AgenrenaAPIError(
                f"Agenrena reply API returned HTTP {error.code}: {raw or error.reason}",
                status=error.code,
                retry_after=_retry_after(error.headers),
            ) from error
        except URLError as error:
            raise AgenrenaAPIError(
                f"Could not reach the Agenrena reply API: {error.reason}"
            ) from error

        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, Mapping) else {"result": parsed}


def authenticated_ws_url(ws_url: str, api_key: str) -> str:
    parsed = urlparse(ws_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = api_key
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


class AgenrenaWebSocketClient:
    def __init__(
        self,
        *,
        ws_url: str,
        api_key: str,
        max_backoff_seconds: float = 30.0,
        ping_interval_seconds: float = 20.0,
        ping_timeout_seconds: float = 20.0,
    ):
        self.ws_url = ws_url
        self.api_key = api_key
        self.max_backoff_seconds = max_backoff_seconds
        self.ping_interval_seconds = ping_interval_seconds
        self.ping_timeout_seconds = ping_timeout_seconds

    async def messages(self) -> AsyncIterator[Mapping[str, Any]]:
        backoff = 1.0
        url = authenticated_ws_url(self.ws_url, self.api_key)
        while True:
            try:
                socket = await WebSocketConnection.connect(
                    url,
                    max_size=2 * 1024 * 1024,
                )
                async with socket:
                    LOGGER.info("Connected to the Agenrena Agent WebSocket.")
                    backoff = 1.0
                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                socket.receive_event(),
                                timeout=self.ping_interval_seconds,
                            )
                        except asyncio.TimeoutError:
                            await socket.ping()
                            raw = await asyncio.wait_for(
                                socket.receive_event(),
                                timeout=self.ping_timeout_seconds,
                            )
                        if raw is None:
                            continue
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            LOGGER.warning("Ignored a non-JSON Agenrena WebSocket event.")
                            continue
                        if isinstance(payload, Mapping):
                            yield payload
                        else:
                            LOGGER.warning("Ignored a non-object Agenrena WebSocket event.")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                jitter = random.uniform(0, min(1.0, backoff * 0.2))
                delay = min(self.max_backoff_seconds, backoff + jitter)
                LOGGER.warning(
                    "Agenrena WebSocket disconnected (%s); reconnecting in %.1fs.",
                    type(error).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                backoff = min(self.max_backoff_seconds, backoff * 2)
