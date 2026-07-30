"""Bounded client for the local Messages-shaped mock server."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mockllm.tokenizer import canonical_json


class ModelClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class Attempt:
    attempt: int
    outcome: str
    status: int | None = None
    error: str | None = None
    delay_seconds: float = 0.0


AttemptCallback = Callable[[Attempt], None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model base URL must be an HTTP(S) URL with a host")
    if parsed.username or parsed.password:
        raise ValueError("model base URL credentials are not allowed")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("model base URL must not contain a path, query, or fragment")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("model base URL contains an invalid port") from exc
    host = parsed.hostname.lower()
    if host == "localhost":
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(result[4][0]).is_loopback
            for result in addresses
        ):
            raise ValueError("model hostname must resolve only to loopback addresses")
    else:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(
                "model host must be localhost or a literal loopback address"
            ) from exc
        if not address.is_loopback:
            raise ValueError("model host must be a loopback address")
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{rendered_host}:{port}"


class MessagesClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        max_attempts: int = 4,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.endpoint = _local_base_url(base_url) + "/v1/messages"
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.sleeper = sleeper
        self._opener = urllib.request.build_opener(_NoRedirect)

    def create_message(
        self,
        request_body: dict[str, Any],
        *,
        scenario: str,
        request_id: str,
        on_attempt: AttemptCallback | None = None,
    ) -> dict[str, Any]:
        callback = on_attempt or (lambda _attempt: None)
        encoded = canonical_json(request_body).encode("utf-8")
        last_error = "model request failed"
        for attempt_number in range(1, self.max_attempts + 1):
            request = urllib.request.Request(
                self.endpoint,
                data=encoded,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Scenario-ID": scenario,
                    "X-Request-ID": request_id,
                },
            )
            try:
                with self._opener.open(
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = response.read()
                payload = json.loads(raw)
                self._validate_response(payload)
                callback(Attempt(attempt_number, "success", status=200))
                return payload
            except urllib.error.HTTPError as exc:
                status = exc.code
                retryable = status in {429, 529}
                delay = self._retry_delay(exc.headers.get("Retry-After"), attempt_number)
                try:
                    error_body = exc.read().decode("utf-8", errors="replace")
                finally:
                    exc.close()
                last_error = f"HTTP {status}: {error_body}"
                callback(
                    Attempt(
                        attempt_number,
                        "retry" if retryable else "failure",
                        status=status,
                        error=last_error,
                        delay_seconds=delay if retryable else 0,
                    )
                )
                if not retryable or attempt_number == self.max_attempts:
                    break
                self.sleeper(delay)
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.IncompleteRead,
                json.JSONDecodeError,
                ModelClientError,
            ) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retryable = attempt_number < self.max_attempts
                delay = min(0.05 * (2 ** (attempt_number - 1)), 0.5)
                callback(
                    Attempt(
                        attempt_number,
                        "retry" if retryable else "failure",
                        error=last_error,
                        delay_seconds=delay if retryable else 0,
                    )
                )
                if not retryable:
                    break
                self.sleeper(delay)
        raise ModelClientError(last_error)

    @staticmethod
    def _retry_delay(header: str | None, attempt: int) -> float:
        if header is not None:
            try:
                return min(max(float(header), 0.0), 2.0)
            except ValueError:
                pass
        return min(0.05 * (2 ** (attempt - 1)), 0.5)

    @staticmethod
    def _validate_response(payload: Any) -> None:
        if not isinstance(payload, dict) or payload.get("type") != "message":
            raise ModelClientError("response is not a Messages API message")
        if payload.get("role") != "assistant":
            raise ModelClientError("response role is not assistant")
        if not isinstance(payload.get("content"), list):
            raise ModelClientError("response content is not a list")
        if payload.get("stop_reason") not in {"tool_use", "end_turn"}:
            raise ModelClientError("response stop_reason is invalid")
        if not isinstance(payload.get("usage"), dict):
            raise ModelClientError("response usage is missing")
