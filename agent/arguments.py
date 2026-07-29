"""Conservative repair and strict validation for model-supplied tool arguments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


MAX_ARGUMENT_BYTES = 64 * 1024


class ArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedArguments:
    value: dict[str, Any]
    repairs: tuple[str, ...] = ()


def _escape_controls_in_strings(raw: str) -> tuple[str, bool]:
    output: list[str] = []
    in_string = False
    escaped = False
    changed = False
    replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in raw:
        if in_string and char in replacements and not escaped:
            output.append(replacements[char])
            changed = True
            continue
        output.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
    return "".join(output), changed


def _remove_trailing_commas(raw: str) -> tuple[str, bool]:
    output: list[str] = []
    in_string = False
    escaped = False
    changed = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "," and not in_string:
            lookahead = index + 1
            while lookahead < len(raw) and raw[lookahead].isspace():
                lookahead += 1
            if lookahead < len(raw) and raw[lookahead] in "}]":
                changed = True
                index += 1
                continue
        output.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        index += 1
    return "".join(output), changed


def _close_truncated_json(raw: str) -> tuple[str, bool]:
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in raw:
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if not stack or stack[-1] != char:
                raise ArgumentError("malformed JSON has mismatched closing delimiters")
            stack.pop()
    suffix = ('"' if in_string else "") + "".join(reversed(stack))
    return raw + suffix, bool(suffix)


def parse_arguments(raw: Any) -> ParsedArguments:
    if isinstance(raw, dict):
        return ParsedArguments(raw)
    if not isinstance(raw, str):
        raise ArgumentError("tool arguments must be an object or JSON object string")
    if len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ArgumentError("tool arguments exceed the 64 KiB limit")

    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ArgumentError("tool arguments must decode to an object")
        return ParsedArguments(decoded)
    except json.JSONDecodeError:
        pass

    repaired = raw
    repairs: list[str] = []
    repaired, changed = _escape_controls_in_strings(repaired)
    if changed:
        repairs.append("escaped_control_characters")
    repaired, changed = _remove_trailing_commas(repaired)
    if changed:
        repairs.append("removed_trailing_commas")
    repaired, changed = _close_truncated_json(repaired)
    if changed:
        repairs.append("closed_truncated_json")

    if not repairs:
        raise ArgumentError("tool arguments are not valid JSON and no safe repair applies")
    try:
        decoded = json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise ArgumentError(f"tool arguments remain invalid after safe repair: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ArgumentError("tool arguments must decode to an object")
    return ParsedArguments(decoded, tuple(repairs))
