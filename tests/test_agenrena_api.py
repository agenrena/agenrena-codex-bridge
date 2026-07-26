from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agenrena_codex_bridge.agenrena import (
    AgenrenaAPIClient,
    authenticated_ws_url,
)
from agenrena_codex_bridge.models import PendingReply


class CaptureHandler(BaseHTTPRequestHandler):
    request_path = None
    request_headers = None
    request_body = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        type(self).request_path = self.path
        type(self).request_headers = self.headers
        type(self).request_body = json.loads(self.rfile.read(length))
        raw = json.dumps({"message_id": "server-message-1"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format, *_args):
        pass


class AgenrenaAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_uses_current_agent_messaging_contract(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = AgenrenaAPIClient(
                api_base=f"http://127.0.0.1:{server.server_port}/api/agent-api",
                api_key="agr_test_secret",
                user_agent=(
                    "agenrena-codex-bridge/0.3.0 "
                    "agenrena-hermes-adapter/0.4.0"
                ),
            )
            result = await client.send_reply(
                PendingReply(
                    inbound_message_id="11111111-1111-1111-1111-111111111111",
                    conversation_id="22222222-2222-2222-2222-222222222222",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    text="Hello from Codex",
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result["message_id"], "server-message-1")
        self.assertEqual(
            CaptureHandler.request_path,
            "/api/agent-api/channels/messages/send/",
        )
        self.assertEqual(
            CaptureHandler.request_headers.get("Authorization"),
            "Bearer agr_test_secret",
        )
        self.assertEqual(
            CaptureHandler.request_headers.get("User-Agent"),
            "agenrena-codex-bridge/0.3.0 agenrena-hermes-adapter/0.4.0",
        )
        self.assertEqual(
            CaptureHandler.request_body,
            {
                "source": "agenrena",
                "conversation_id": "22222222-2222-2222-2222-222222222222",
                "text": "Hello from Codex",
                "message_id": "codex-11111111-1111-1111-1111-111111111111",
                "reply_to_message_id": "11111111-1111-1111-1111-111111111111",
            },
        )

    async def test_ws_auth_uses_url_encoded_token_query(self):
        url = authenticated_ws_url(
            "wss://api.agenrena.com/ws/agent/events/?existing=1",
            "agr_key+with/symbols",
        )
        self.assertEqual(
            url,
            "wss://api.agenrena.com/ws/agent/events/?existing=1&token=agr_key%2Bwith%2Fsymbols",
        )


if __name__ == "__main__":
    unittest.main()
