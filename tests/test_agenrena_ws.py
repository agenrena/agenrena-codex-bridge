from __future__ import annotations

import asyncio
import json
import unittest

from websockets.asyncio.server import serve

from agenrena_codex_bridge.agenrena import AgenrenaWebSocketClient


class AgenrenaWebSocketClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_authenticates_with_query_token_and_receives_payload(self):
        observed_path = asyncio.get_running_loop().create_future()
        payload = {
            "id": "message-1",
            "conversation_id": "conversation-1",
            "message_type": "text",
            "text": "hello",
        }

        async def handler(connection):
            observed_path.set_result(connection.request.path)
            await connection.send(json.dumps(payload))
            await connection.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AgenrenaWebSocketClient(
                ws_url=f"ws://127.0.0.1:{port}/ws/agent/events/",
                api_key="agr_key+with/symbols",
            )
            messages = client.messages()
            received = await asyncio.wait_for(messages.__anext__(), timeout=2)
            await messages.aclose()

        self.assertEqual(received, payload)
        self.assertEqual(
            await observed_path,
            "/ws/agent/events/?token=agr_key%2Bwith%2Fsymbols",
        )

    async def test_client_handles_server_ping_and_fragmented_text(self):
        payload = {
            "id": "message-fragmented",
            "conversation_id": "conversation-1",
            "message_type": "text",
            "text": "fragmented",
        }
        raw = json.dumps(payload)

        async def handler(connection):
            pong = await connection.ping(b"agenrena-probe")
            await asyncio.wait_for(pong, timeout=1)
            await connection.send([raw[:10], raw[10:]])
            await connection.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AgenrenaWebSocketClient(
                ws_url=f"ws://127.0.0.1:{port}/ws/agent/events/",
                api_key="agr_test_key",
            )
            messages = client.messages()
            received = await asyncio.wait_for(messages.__anext__(), timeout=2)
            await messages.aclose()

        self.assertEqual(received, payload)

    async def test_client_keepalive_ping_allows_later_message(self):
        payload = {
            "id": "message-after-ping",
            "conversation_id": "conversation-1",
            "message_type": "text",
            "text": "still connected",
        }

        async def handler(connection):
            await asyncio.sleep(0.08)
            await connection.send(json.dumps(payload))
            await connection.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AgenrenaWebSocketClient(
                ws_url=f"ws://127.0.0.1:{port}/ws/agent/events/",
                api_key="agr_test_key",
                ping_interval_seconds=0.02,
                ping_timeout_seconds=0.5,
            )
            messages = client.messages()
            received = await asyncio.wait_for(messages.__anext__(), timeout=2)
            await messages.aclose()

        self.assertEqual(received, payload)


if __name__ == "__main__":
    unittest.main()
