from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
from typing import Optional, Union
from urllib.parse import urlsplit


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HTTP_HEADER_BYTES = 64 * 1024


class WebSocketError(RuntimeError):
    pass


class WebSocketClosed(WebSocketError):
    pass


class WebSocketConnection:
    """Small RFC 6455 client for text events, ping/pong, and close handling."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_size: int,
    ):
        self._reader = reader
        self._writer = writer
        self._max_size = max_size
        self._fragment_opcode: Optional[int] = None
        self._fragment_data = bytearray()
        self._close_sent = False

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        max_size: int = 2 * 1024 * 1024,
    ) -> "WebSocketConnection":
        parsed = urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise WebSocketError(
                "WebSocket URL must be an absolute ws:// or wss:// URL."
            )

        secure = parsed.scheme == "wss"
        port = parsed.port or (443 if secure else 80)
        tls_context = ssl.create_default_context() if secure else None
        reader, writer = await asyncio.open_connection(
            parsed.hostname,
            port,
            ssl=tls_context,
            server_hostname=parsed.hostname if secure else None,
        )

        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"

            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            default_port = 443 if secure else 80
            host_header = host if port == default_port else f"{host}:{port}"
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "User-Agent: agenrena-codex-bridge/0.3.0\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            try:
                raw_headers = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
                raise WebSocketError(
                    "WebSocket server returned an incomplete HTTP upgrade response."
                ) from error
            if len(raw_headers) > MAX_HTTP_HEADER_BYTES:
                raise WebSocketError("WebSocket HTTP upgrade response is too large.")

            status, headers = _parse_upgrade_response(raw_headers)
            if status != 101:
                raise WebSocketError(
                    f"WebSocket server rejected the upgrade with HTTP {status}."
                )
            if headers.get("upgrade", "").lower() != "websocket":
                raise WebSocketError(
                    "WebSocket upgrade response is missing Upgrade: websocket."
                )
            connection_tokens = {
                item.strip().lower()
                for item in headers.get("connection", "").split(",")
            }
            if "upgrade" not in connection_tokens:
                raise WebSocketError(
                    "WebSocket upgrade response is missing Connection: Upgrade."
                )
            expected_accept = base64.b64encode(
                hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
            ).decode("ascii")
            if headers.get("sec-websocket-accept") != expected_accept:
                raise WebSocketError(
                    "WebSocket server returned an invalid Sec-WebSocket-Accept."
                )
            return cls(reader, writer, max_size=max_size)
        except BaseException:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            raise

    async def __aenter__(self) -> "WebSocketConnection":
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.close()

    async def receive_event(self) -> Optional[Union[str, bytes]]:
        """Return one message, or None after valid control/fragment activity."""

        final, opcode, payload = await self._read_frame()

        if opcode == 0x8:
            if len(payload) == 1:
                raise WebSocketError("WebSocket close frame has an invalid payload.")
            if not self._close_sent:
                await self._send_frame(0x8, payload)
                self._close_sent = True
            raise WebSocketClosed("WebSocket peer closed the connection.")
        if opcode == 0x9:
            await self._send_frame(0xA, payload)
            return None
        if opcode == 0xA:
            return None

        if opcode == 0x0:
            if self._fragment_opcode is None:
                raise WebSocketError("Unexpected WebSocket continuation frame.")
            self._fragment_data.extend(payload)
            self._check_message_size(len(self._fragment_data))
            if not final:
                return None
            opcode = self._fragment_opcode
            payload = bytes(self._fragment_data)
            self._fragment_opcode = None
            self._fragment_data.clear()
            return _decode_message(opcode, payload)

        if opcode not in {0x1, 0x2}:
            raise WebSocketError(f"Unsupported WebSocket opcode: {opcode}.")
        if self._fragment_opcode is not None:
            raise WebSocketError(
                "Received a new WebSocket message before fragments completed."
            )
        if not final:
            self._fragment_opcode = opcode
            self._fragment_data.extend(payload)
            self._check_message_size(len(self._fragment_data))
            return None

        self._check_message_size(len(payload))
        return _decode_message(opcode, payload)

    async def ping(self) -> None:
        await self._send_frame(0x9, os.urandom(4))

    async def close(self) -> None:
        if not self._writer.is_closing() and not self._close_sent:
            try:
                await self._send_frame(0x8, b"\x03\xe8")
                self._close_sent = True
            except (ConnectionError, WebSocketError):
                pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            first, second = await self._reader.readexactly(2)
            final = bool(first & 0x80)
            reserved = first & 0x70
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if reserved:
                raise WebSocketError(
                    "WebSocket frame uses an extension that was not negotiated."
                )
            if masked:
                raise WebSocketError("WebSocket server frames must not be masked.")
            if opcode >= 0x8 and (not final or length > 125):
                raise WebSocketError("Invalid fragmented WebSocket control frame.")

            if length == 126:
                length = int.from_bytes(
                    await self._reader.readexactly(2),
                    "big",
                )
            elif length == 127:
                extended = await self._reader.readexactly(8)
                if extended[0] & 0x80:
                    raise WebSocketError("Invalid 64-bit WebSocket frame length.")
                length = int.from_bytes(extended, "big")

            self._check_message_size(length)
            payload = await self._reader.readexactly(length)
            return final, opcode, payload
        except asyncio.IncompleteReadError as error:
            raise WebSocketClosed("WebSocket connection ended.") from error

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._writer.is_closing():
            raise WebSocketClosed("WebSocket connection is closed.")
        if len(payload) > self._max_size:
            raise WebSocketError("WebSocket outbound frame is too large.")

        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = bytes((first, 0x80 | length))
        elif length < (1 << 16):
            header = bytes((first, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            header = bytes((first, 0x80 | 127)) + length.to_bytes(8, "big")

        mask = os.urandom(4)
        masked = bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(payload)
        )
        self._writer.write(header + mask + masked)
        await self._writer.drain()

    def _check_message_size(self, size: int) -> None:
        if size > self._max_size:
            raise WebSocketError(
                f"WebSocket message exceeds the {self._max_size}-byte limit."
            )


def _parse_upgrade_response(raw: bytes) -> tuple[int, dict[str, str]]:
    try:
        text = raw.decode("iso-8859-1")
        lines = text.split("\r\n")
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or not status_parts[0].startswith("HTTP/1."):
            raise ValueError
        status = int(status_parts[1])
    except (UnicodeDecodeError, ValueError, IndexError) as error:
        raise WebSocketError(
            "WebSocket server returned an invalid HTTP upgrade response."
        ) from error

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise WebSocketError("WebSocket server returned an invalid HTTP header.")
        name, value = line.split(":", 1)
        normalized_name = name.strip().lower()
        normalized_value = value.strip()
        if normalized_name in headers:
            headers[normalized_name] = (
                f"{headers[normalized_name]},{normalized_value}"
            )
        else:
            headers[normalized_name] = normalized_value
    return status, headers


def _decode_message(opcode: int, payload: bytes) -> Union[str, bytes]:
    if opcode == 0x2:
        return payload
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WebSocketError("WebSocket text message is not valid UTF-8.") from error
