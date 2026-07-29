"""Threaded local HTTP server exposing a hostile Messages-shaped API."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import socket
import threading
from collections.abc import Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mockllm.protocol import (
    MAX_REQUEST_BYTES,
    ProtocolError,
    canonical_json,
    error_body,
    logical_request_id,
    validate_request,
)
from mockllm.scenarios import ScenarioEngine


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        seed: int = 20260728,
        verbose: bool = False,
    ):
        super().__init__(server_address, MockRequestHandler)
        self.engine = ScenarioEngine()
        self.seed = seed
        self.verbose = verbose
        self._attempts: dict[tuple[str, str], int] = {}
        self._attempt_lock = threading.Lock()

    def next_attempt(self, scenario_id: str, request_id: str) -> int:
        key = (scenario_id, request_id)
        with self._attempt_lock:
            attempt = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempt
            return attempt

    def reset_attempts(self) -> None:
        with self._attempt_lock:
            self._attempts.clear()


class MockRequestHandler(BaseHTTPRequestHandler):
    server: MockServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "scenarios": sorted(
                        self.server.engine.scenarios,
                        key=lambda value: int(value[1:]),
                    ),
                },
            )
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            error_body("not_found", f"unknown path {self.path!r}"),
        )

    def do_POST(self) -> None:
        if self.path == "/admin/reset":
            self.server.reset_attempts()
            self._send_json(HTTPStatus.OK, {"status": "reset"})
            return
        if self.path != "/v1/messages":
            self._send_json(
                HTTPStatus.NOT_FOUND,
                error_body("not_found", f"unknown path {self.path!r}"),
            )
            return

        payload = self._read_payload()
        if payload is None:
            return
        try:
            request = validate_request(payload)
        except ProtocolError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                error_body(exc.error_type, str(exc)),
            )
            return

        scenario_id = self.headers.get("X-Scenario-ID") or request["metadata"].get(
            "scenario", "S1"
        )
        if scenario_id not in self.server.engine.scenarios:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                error_body("unknown_scenario", f"unknown scenario {scenario_id!r}"),
            )
            return

        request_id = (
            self.headers.get("X-Request-ID") or logical_request_id(request)
        )
        attempt = self.server.next_attempt(scenario_id, request_id)

        if scenario_id == "S6" and attempt == 1:
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                error_body("rate_limit_error", "simulated 429; retry later"),
                headers={"Retry-After": "0"},
            )
            return
        if scenario_id == "S6" and attempt == 2:
            self._send_json(
                529,
                error_body("overloaded_error", "simulated temporary overload"),
            )
            return

        response = self.server.engine.response(scenario_id, request, request_id)
        response_bytes = canonical_json(response).encode("utf-8")

        if scenario_id == "S5" and attempt == 1:
            cut = self._random_cut(response_bytes, request_id)
            self._send_partial(response_bytes, cut)
            return
        if scenario_id == "S12" and attempt == 1 and not self._has_tool_history(request):
            marker = b'"name":"write_file"'
            first = response_bytes.find(marker)
            second = response_bytes.find(marker, first + len(marker))
            cut = second if second > 0 else max(1, len(response_bytes) // 3)
            self._send_partial(response_bytes, cut)
            return

        self._send_bytes(HTTPStatus.OK, response_bytes)

    def _read_payload(self) -> Any | None:
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                error_body("invalid_request_error", "Content-Type must be application/json"),
            )
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                error_body("invalid_request_error", "invalid request body size"),
            )
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                error_body("invalid_request_error", f"invalid JSON: {exc}"),
            )
            return None

    def _random_cut(self, body: bytes, request_id: str) -> int:
        digest = hashlib.sha256(
            f"{self.server.seed}:{request_id}".encode("utf-8")
        ).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        return rng.randint(max(1, len(body) // 8), max(1, len(body) * 7 // 8))

    @staticmethod
    def _has_tool_history(request: dict[str, Any]) -> bool:
        return any(
            isinstance(message.get("content"), list)
            and any(block.get("type") == "tool_use" for block in message["content"])
            for message in request["messages"]
            if message.get("role") == "assistant"
        )

    def _send_partial(self, body: bytes, cut: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body[:cut])
            self.wfile.flush()
        except OSError:
            pass
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.close_connection = True

    def _send_json(
        self,
        status: int,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(status, canonical_json(body).encode("utf-8"), headers=headers)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    seed: int = 20260728,
    verbose: bool = False,
) -> MockServer:
    return MockServer((host, port), seed=seed, verbose=verbose)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mockllm")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = create_server(args.host, args.port, seed=args.seed, verbose=args.verbose)
    host, port = server.server_address
    print(f"mockllm listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
