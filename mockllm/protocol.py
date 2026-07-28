"""Validation and response helpers for the local Messages-shaped API."""

from __future__ import annotations

import hashlib
from typing import Any

from mockllm.tokenizer import canonical_json, count_tokens


MODEL_NAME = "mock-hostile-1"
CONTEXT_LIMIT = 8_000
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class ProtocolError(ValueError):
    def __init__(self, message: str, *, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.error_type = error_type


def error_body(error_type: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def _validate_content(content: Any, *, field: str) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise ProtocolError(f"{field} must be a string or content-block list")
    for index, block in enumerate(content):
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise ProtocolError(f"{field}[{index}] must be a typed object")


def validate_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolError("request body must be a JSON object")

    model = payload.get("model", MODEL_NAME)
    if model != MODEL_NAME:
        raise ProtocolError(f"unsupported model {model!r}")

    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise ProtocolError("max_tokens must be a positive integer")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProtocolError("messages must be a non-empty list")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ProtocolError(f"messages[{index}] must be an object")
        if message.get("role") not in {"user", "assistant"}:
            raise ProtocolError(f"messages[{index}].role is invalid")
        _validate_content(message.get("content"), field=f"messages[{index}].content")

    system = payload.get("system", "")
    _validate_content(system, field="system")

    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        raise ProtocolError("tools must be a list")
    seen_names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ProtocolError(f"tools[{index}] must be an object")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ProtocolError(f"tools[{index}].name must be a non-empty string")
        if name in seen_names:
            raise ProtocolError(f"duplicate tool name {name!r}")
        seen_names.add(name)
        if not isinstance(tool.get("input_schema"), dict):
            raise ProtocolError(f"tools[{index}].input_schema must be an object")

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ProtocolError("metadata must be an object")

    normalized = dict(payload)
    normalized["model"] = model
    normalized["system"] = system
    normalized["tools"] = tools
    normalized["metadata"] = metadata

    input_tokens = request_token_count(normalized)
    if input_tokens > CONTEXT_LIMIT:
        raise ProtocolError(
            f"input uses {input_tokens} tokens; hard limit is {CONTEXT_LIMIT}",
            error_type="context_length_exceeded",
        )
    normalized["_input_tokens"] = input_tokens
    return normalized


def request_token_count(payload: dict[str, Any]) -> int:
    counted = {
        "system": payload.get("system", ""),
        "messages": payload.get("messages", []),
        "tools": payload.get("tools", []),
    }
    return count_tokens(counted)


def logical_request_id(payload: dict[str, Any]) -> str:
    metadata_id = payload.get("metadata", {}).get("request_id")
    if isinstance(metadata_id, str) and metadata_id:
        return metadata_id
    digest_payload = {key: value for key, value in payload.items() if key != "_input_tokens"}
    return hashlib.sha256(canonical_json(digest_payload).encode("utf-8")).hexdigest()[:24]


def make_message(
    *,
    message_id: str,
    content: list[dict[str, Any]],
    stop_reason: str,
    input_tokens: int,
) -> dict[str, Any]:
    output_tokens = count_tokens(content)
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": MODEL_NAME,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
