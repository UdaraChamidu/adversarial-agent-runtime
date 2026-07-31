"""Canonical event representation and hash-chain helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from mockllm.tokenizer import canonical_json


GENESIS_HASH = "0" * 64
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError(
            "run_id must be 1-128 ASCII letters, digits, dots, underscores, or "
            "hyphens, and must start with a letter or digit"
        )
    return run_id


@dataclass(frozen=True)
class Event:
    run_id: str
    seq: int
    event_id: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    event_hash: str
    created_at: str


def calculate_event_hash(
    *,
    run_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
    prev_hash: str,
) -> str:
    material = {
        "event_type": event_type,
        "payload": payload,
        "prev_hash": prev_hash,
        "run_id": run_id,
        "seq": seq,
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def event_id(run_id: str, seq: int, event_hash: str) -> str:
    return f"evt_{run_id}_{seq}_{event_hash[:12]}"


def occurrence_key(run_id: str, response_seq: int, tool_index: int) -> str:
    if response_seq <= 0 or tool_index < 0:
        raise ValueError("invalid tool occurrence coordinates")
    material = f"{run_id}:{response_seq}:{tool_index}".encode("utf-8")
    return "tool_" + hashlib.sha256(material).hexdigest()[:32]
