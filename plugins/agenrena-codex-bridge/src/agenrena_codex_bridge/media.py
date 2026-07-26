from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

from .models import IncomingMedia


MAX_MEDIA_COUNT = 9
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
DOWNLOAD_ATTEMPTS = 3
MAX_CONCURRENT_DOWNLOADS = 4
STALE_MEDIA_SECONDS = 24 * 60 * 60
READ_CHUNK_BYTES = 64 * 1024

Resolver = Callable[..., list[tuple]]


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaterializedMedia:
    kind: str
    path: Path
    mime_type: str
    size_bytes: int


@dataclass
class MaterializedBatch:
    directory: Path
    items: tuple[MaterializedMedia, ...]
    _cleaned: bool = False

    async def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        await asyncio.to_thread(shutil.rmtree, self.directory, True)


def safe_url_for_log(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
            "",
        )
    )


def _validate_https_url(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    allow_private_hosts: bool = False,
) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MediaError("Media URLs must be absolute https:// URLs without credentials.")

    try:
        addresses = resolver(
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise MediaError(
            f"Could not resolve media host {parsed.hostname}."
        ) from error

    if not addresses:
        raise MediaError(f"Media host {parsed.hostname} did not resolve.")
    if allow_private_hosts:
        return

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except (IndexError, TypeError, ValueError) as error:
            raise MediaError("Media host resolved to an invalid address.") from error
        if not ip.is_global:
            raise MediaError("Media URL resolved to a non-public address.")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        *,
        resolver: Resolver,
        allow_private_hosts: bool,
    ):
        super().__init__()
        self._resolver = resolver
        self._allow_private_hosts = allow_private_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_https_url(
            newurl,
            resolver=self._resolver,
            allow_private_hosts=self._allow_private_hosts,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _detected_image_type(data: bytes) -> Optional[tuple[str, str]]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _content_length(headers: Message) -> Optional[int]:
    value = headers.get("Content-Length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class MediaStore:
    def __init__(
        self,
        root: Path,
        *,
        max_media_count: int = MAX_MEDIA_COUNT,
        max_media_bytes: int = MAX_MEDIA_BYTES,
        max_total_bytes: int = MAX_TOTAL_MEDIA_BYTES,
        timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
        attempts: int = DOWNLOAD_ATTEMPTS,
        max_concurrent_downloads: int = MAX_CONCURRENT_DOWNLOADS,
        resolver: Resolver = socket.getaddrinfo,
        allow_private_hosts: bool = False,
        opener: Optional[OpenerDirector] = None,
    ):
        self.root = root
        self.max_media_count = max_media_count
        self.max_media_bytes = max_media_bytes
        self.max_total_bytes = max_total_bytes
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._download_semaphore = asyncio.Semaphore(max_concurrent_downloads)
        self.resolver = resolver
        self.allow_private_hosts = allow_private_hosts
        self.opener = opener or build_opener(
            HTTPSHandler(context=ssl.create_default_context()),
            _SafeRedirectHandler(
                resolver=resolver,
                allow_private_hosts=allow_private_hosts,
            ),
        )

    async def prepare(self) -> None:
        await asyncio.to_thread(self._prepare_sync)

    def _prepare_sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        cutoff = time.time() - STALE_MEDIA_SECONDS
        for candidate in self.root.glob("message-*"):
            try:
                if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                    shutil.rmtree(candidate)
            except OSError:
                continue

    async def materialize(
        self,
        media: Sequence[IncomingMedia],
    ) -> MaterializedBatch:
        async with self._download_semaphore:
            return await asyncio.to_thread(self._materialize_sync, tuple(media))

    def _materialize_sync(
        self,
        media: tuple[IncomingMedia, ...],
    ) -> MaterializedBatch:
        if not media:
            raise MediaError("Cannot materialize an empty media list.")
        if len(media) > self.max_media_count:
            raise MediaError(
                f"Message contains more than {self.max_media_count} media items."
            )

        self._prepare_sync()
        directory = Path(tempfile.mkdtemp(prefix="message-", dir=self.root))
        os.chmod(directory, 0o700)
        items: list[MaterializedMedia] = []
        total_bytes = 0
        try:
            for index, source in enumerate(media):
                data, mime_type, extension = self._download(source.url)
                total_bytes += len(data)
                if total_bytes > self.max_total_bytes:
                    raise MediaError(
                        f"Message media exceeds the {self.max_total_bytes}-byte total limit."
                    )

                path = directory / f"{index + 1}{extension}"
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                items.append(
                    MaterializedMedia(
                        kind=source.kind,
                        path=path.resolve(),
                        mime_type=mime_type,
                        size_bytes=len(data),
                    )
                )
            return MaterializedBatch(directory=directory, items=tuple(items))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _download(self, url: str) -> tuple[bytes, str, str]:
        _validate_https_url(
            url,
            resolver=self.resolver,
            allow_private_hosts=self.allow_private_hosts,
        )
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.attempts + 1):
            try:
                return self._download_once(url)
            except HTTPError as error:
                last_error = error
                if error.code != 429 and error.code < 500:
                    break
            except (TimeoutError, URLError, OSError) as error:
                last_error = error
            except MediaError:
                raise

            if attempt < self.attempts:
                time.sleep(min(2 ** (attempt - 1), 4))

        raise MediaError(
            f"Could not download media from {safe_url_for_log(url)}."
        ) from last_error

    def _download_once(self, url: str) -> tuple[bytes, str, str]:
        request = Request(
            url,
            headers={
                "Accept": "image/png,image/jpeg,image/gif,image/webp",
                "User-Agent": "agenrena-codex-bridge/0.3.0",
            },
        )
        with self.opener.open(request, timeout=self.timeout_seconds) as response:
            final_url = response.geturl()
            _validate_https_url(
                final_url,
                resolver=self.resolver,
                allow_private_hosts=self.allow_private_hosts,
            )
            announced_size = _content_length(response.headers)
            if announced_size is not None and announced_size > self.max_media_bytes:
                raise MediaError(
                    f"Media exceeds the {self.max_media_bytes}-byte size limit."
                )

            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_media_bytes:
                    raise MediaError(
                        f"Media exceeds the {self.max_media_bytes}-byte size limit."
                    )
                chunks.append(chunk)

        data = b"".join(chunks)
        detected = _detected_image_type(data)
        if detected is None:
            raise MediaError("Downloaded media is not a supported image.")
        mime_type, extension = detected
        return data, mime_type, extension
