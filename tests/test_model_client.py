from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.model_client import MessagesClient, ModelClientError


class _RedirectingModelHandler(BaseHTTPRequestHandler):
    requests = 0

    def log_message(self, format, *args):
        return

    def do_POST(self):
        type(self).requests += 1
        if self.path == "/v1/messages":
            self.send_response(307)
            self.send_header("Location", "/redirect-target")
            self.end_headers()
            return
        body = json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "redirect followed"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MessagesClientBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectingModelHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_accepts_only_localhost_or_literal_loopback(self) -> None:
        for base_url in (
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://[::1]:8765",
        ):
            with self.subTest(base_url=base_url):
                client = MessagesClient(base_url)
                self.assertTrue(client.endpoint.endswith("/v1/messages"))

        for base_url in (
            "https://example.com",
            "http://192.0.2.1:8765",
            "http://user@127.0.0.1:8765",
            "http://127.0.0.1:8765/api",
            "http://127.0.0.1:8765?target=external",
            "file:///tmp/mock",
            "http://2130706433",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    MessagesClient(base_url)

    def test_model_transport_refuses_redirects(self) -> None:
        _RedirectingModelHandler.requests = 0
        client = MessagesClient(self.base_url, max_attempts=1)
        with self.assertRaises(ModelClientError):
            client.create_message(
                {"messages": []},
                scenario="S1",
                request_id="redirect-test",
            )
        self.assertEqual(_RedirectingModelHandler.requests, 1)


if __name__ == "__main__":
    unittest.main()
