from __future__ import annotations

import tempfile
import unittest
from email.message import Message
from pathlib import Path

from agenrena_codex_bridge.media import (
    MediaError,
    MediaStore,
    safe_url_for_log,
)
from agenrena_codex_bridge.models import IncomingMedia


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"png-data"
JPEG_BYTES = b"\xff\xd8\xff" + b"jpeg-data"


def public_resolver(host, port, type):
    return [
        (
            2,
            type,
            6,
            "",
            ("93.184.216.34", port),
        )
    ]


def private_resolver(host, port, type):
    return [
        (
            2,
            type,
            6,
            "",
            ("127.0.0.1", port),
        )
    ]


class FakeResponse:
    def __init__(self, url: str, data: bytes):
        self._url = url
        self._data = data
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Length"] = str(len(data))

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def geturl(self):
        return self._url

    def read(self, size: int):
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)

    def open(self, request, timeout):
        return self.responses.pop(0)


class MediaStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_materializes_images_and_stickers_with_detected_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            store = MediaStore(
                root,
                resolver=public_resolver,
                opener=FakeOpener(
                    [
                        FakeResponse("https://cdn.example/photo", JPEG_BYTES),
                        FakeResponse("https://cdn.example/sticker", PNG_BYTES),
                    ]
                ),
            )

            batch = await store.materialize(
                [
                    IncomingMedia(
                        kind="image",
                        url="https://cdn.example/photo?secret=one",
                        mime_type="image/jpeg",
                    ),
                    IncomingMedia(
                        kind="sticker",
                        url="https://cdn.example/sticker?secret=two",
                        mime_type="image/png",
                    ),
                ]
            )

            self.assertEqual([item.kind for item in batch.items], ["image", "sticker"])
            self.assertEqual(
                [item.path.suffix for item in batch.items],
                [".jpg", ".png"],
            )
            self.assertTrue(all(item.path.is_file() for item in batch.items))
            await batch.cleanup()
            self.assertFalse(batch.directory.exists())

    async def test_rejects_private_hosts_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MediaStore(
                Path(directory) / "media",
                resolver=private_resolver,
                opener=FakeOpener([]),
            )
            with self.assertRaisesRegex(MediaError, "non-public"):
                await store.materialize(
                    [
                        IncomingMedia(
                            kind="image",
                            url="https://private.example/image.png",
                            mime_type="image/png",
                        )
                    ]
                )

    async def test_rejects_unencrypted_media_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MediaStore(
                Path(directory) / "media",
                resolver=public_resolver,
                opener=FakeOpener([]),
            )
            with self.assertRaisesRegex(MediaError, "https://"):
                await store.materialize(
                    [
                        IncomingMedia(
                            kind="image",
                            url="http://cdn.example/image.png",
                            mime_type="image/png",
                        )
                    ]
                )

    async def test_rejects_non_images_and_cleans_partial_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            store = MediaStore(
                root,
                resolver=public_resolver,
                opener=FakeOpener(
                    [FakeResponse("https://cdn.example/not-image", b"<html>error")]
                ),
            )
            with self.assertRaisesRegex(MediaError, "supported image"):
                await store.materialize(
                    [
                        IncomingMedia(
                            kind="image",
                            url="https://cdn.example/not-image",
                            mime_type="image/png",
                        )
                    ]
                )
            self.assertEqual(list(root.glob("message-*")), [])

    async def test_enforces_count_and_size_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            too_many = [
                IncomingMedia(
                    kind="image",
                    url=f"https://cdn.example/{index}.png",
                    mime_type="image/png",
                )
                for index in range(10)
            ]
            store = MediaStore(
                root,
                resolver=public_resolver,
                opener=FakeOpener([]),
            )
            with self.assertRaisesRegex(MediaError, "more than 9"):
                await store.materialize(too_many)

            oversized_store = MediaStore(
                root,
                max_media_bytes=4,
                resolver=public_resolver,
                opener=FakeOpener(
                    [FakeResponse("https://cdn.example/image.png", PNG_BYTES)]
                ),
            )
            with self.assertRaisesRegex(MediaError, "size limit"):
                await oversized_store.materialize(
                    [
                        IncomingMedia(
                            kind="image",
                            url="https://cdn.example/image.png",
                            mime_type="image/png",
                        )
                    ]
                )

            total_store = MediaStore(
                root,
                max_media_bytes=len(PNG_BYTES),
                max_total_bytes=len(PNG_BYTES) + 1,
                resolver=public_resolver,
                opener=FakeOpener(
                    [
                        FakeResponse("https://cdn.example/one.png", PNG_BYTES),
                        FakeResponse("https://cdn.example/two.png", PNG_BYTES),
                    ]
                ),
            )
            with self.assertRaisesRegex(MediaError, "total limit"):
                await total_store.materialize(
                    [
                        IncomingMedia(
                            kind="image",
                            url="https://cdn.example/one.png",
                            mime_type="image/png",
                        ),
                        IncomingMedia(
                            kind="image",
                            url="https://cdn.example/two.png",
                            mime_type="image/png",
                        ),
                    ]
                )

    def test_safe_log_url_removes_signed_query_and_fragment(self):
        self.assertEqual(
            safe_url_for_log(
                "https://cdn.example/path/image.png?signature=secret#fragment"
            ),
            "https://cdn.example/path/image.png",
        )
