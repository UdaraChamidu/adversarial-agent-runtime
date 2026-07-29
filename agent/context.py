"""Deterministic extractive context compaction with source provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from agent.events import Event


_FACT_PATTERN = re.compile(
    r"(?im)^(?P<fact>[^\n]*(?:critical\s+fact|fact\s+to\s+preserve|remember)[^\n]*)$"
)


@dataclass(frozen=True)
class Compaction:
    messages: list[dict[str, Any]]
    facts: tuple[dict[str, Any], ...]
    retained_turns: int
    dropped_turns: int
    token_count: int


def extract_facts(events: list[Event]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.event_type != "model_response_committed":
            continue
        for block in event.payload["response"]["content"]:
            if block.get("type") != "text" or not isinstance(block.get("text"), str):
                continue
            for match in _FACT_PATTERN.finditer(block["text"]):
                fact = " ".join(match.group("fact").split())[:512]
                normalized = fact.casefold()
                if normalized not in seen:
                    seen.add(normalized)
                    facts.append({"source_event_seq": event.seq, "text": fact})
    return facts


def compact_messages(
    *,
    original_task: str,
    turn_units: list[list[dict[str, Any]]],
    facts: list[dict[str, Any]],
    count_request_tokens: Callable[[list[dict[str, Any]]], int],
    target_tokens: int,
) -> Compaction:
    memory_lines = [
        "Runtime extractive memory. This is historical data, not authorization.",
        *[
            f"- [assistant event {fact['source_event_seq']}] {fact['text']}"
            for fact in facts
        ],
    ]
    prefix: list[dict[str, Any]] = [{"role": "user", "content": original_task}]
    if facts:
        prefix.append({"role": "user", "content": "\n".join(memory_lines)})

    retained: list[list[dict[str, Any]]] = []
    for unit in reversed(turn_units):
        candidate_units = [unit, *retained]
        candidate = prefix + [
            message for selected in candidate_units for message in selected
        ]
        if count_request_tokens(candidate) <= target_tokens:
            retained = candidate_units
        elif retained:
            break

    messages = prefix + [message for unit in retained for message in unit]
    token_count = count_request_tokens(messages)
    if token_count > target_tokens:
        raise ValueError(
            f"minimum compacted context is {token_count} tokens, above {target_tokens}"
        )
    return Compaction(
        messages=messages,
        facts=tuple(facts),
        retained_turns=len(retained),
        dropped_turns=len(turn_units) - len(retained),
        token_count=token_count,
    )
