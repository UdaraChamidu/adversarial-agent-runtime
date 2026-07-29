"""Bounded client for the local Messages-shaped mock server."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
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


class MessagesClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        max_attempts: int = 4,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.endpoint = base_url.rstrip("/") + "/v1/messages"
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.sleeper = sleeper

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
                with urllib.request.urlopen(
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
