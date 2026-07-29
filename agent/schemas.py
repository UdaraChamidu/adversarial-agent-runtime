"""Exact tool schemas used for both model prompting and runtime validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    python_type: type
    max_length: int


_FIELDS: dict[str, dict[str, Field]] = {
    "read_file": {"path": Field(str, 4_096)},
    "write_file": {
        "path": Field(str, 4_096),
        "content": Field(str, 64 * 1024),
    },
    "run_python": {"code": Field(str, 64 * 1024)},
    "http_get": {"url": Field(str, 8_192)},
    "send_email": {
        "to": Field(str, 320),
        "subject": Field(str, 998),
        "body": Field(str, 64 * 1024),
    },
}

_DESCRIPTIONS = {
    "read_file": "Read a UTF-8 file below the workspace root.",
    "write_file": "Atomically write a UTF-8 file below the workspace root.",
    "run_python": "Run bounded Python code without filesystem or network access.",
    "http_get": "GET an explicitly allow-listed localhost URL.",
    "send_email": "Record one pre-authorized simulated email exactly once.",
}


def validate_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, str]:
    try:
        fields = _FIELDS[tool_name]
    except KeyError as exc:
        raise SchemaError(f"unknown tool {tool_name!r}") from exc
    if set(arguments) != set(fields):
        missing = sorted(set(fields) - set(arguments))
        extra = sorted(set(arguments) - set(fields))
        raise SchemaError(f"schema mismatch: missing={missing}, extra={extra}")
    validated: dict[str, str] = {}
    for name, field in fields.items():
        value = arguments[name]
        if type(value) is not field.python_type:
            raise SchemaError(f"{name} must be {field.python_type.__name__}")
        if not value:
            raise SchemaError(f"{name} must not be empty")
        if len(value.encode("utf-8")) > field.max_length:
            raise SchemaError(f"{name} exceeds its size limit")
        validated[name] = value
    return validated


def tool_definitions() -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for name, fields in _FIELDS.items():
        definitions.append(
            {
                "name": name,
                "description": _DESCRIPTIONS[name],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        field_name: {"type": "string"}
                        for field_name in fields
                    },
                    "required": list(fields),
                    "additionalProperties": False,
                },
            }
        )
    return definitions
