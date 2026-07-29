"""Offline verification and reproduction of recorded runtime decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from agent.events import occurrence_key
from agent.store import EventStore
from mockllm.protocol import request_token_count
from mockllm.tokenizer import canonical_json


@dataclass(frozen=True)
class ReplayReport:
    run_id: str
    matches_recording: bool
    decision_count: int
    decision_hash: str
    terminal_status: str
    errors: tuple[str, ...]


def replay_run(store: EventStore, run_id: str) -> ReplayReport:
    store.verify_event_chain(run_id)
    events = store.load_events(run_id)
    errors: list[str] = []
    decisions: list[dict[str, Any]] = []
    planned: set[str] = set()
    results = {
        event.payload["occurrence_key"]: event
        for event in events if event.event_type == "tool_result_committed"
    }
    for event in events:
        if event.event_type == "model_request_planned":
            request_id = event.payload["request_id"]
            planned.add(request_id)
            request = event.payload["request"]
            tokens = request_token_count(request)
            if tokens > 8_000:
                errors.append(f"request {request_id} exceeded context limit: {tokens}")
            decisions.append(
                {"kind": "request", "request_id": request_id, "tokens": tokens}
            )
        elif event.event_type == "model_response_committed":
            request_id = event.payload["request_id"]
            if request_id not in planned:
                errors.append(f"response without planned request: {request_id}")
            calls = []
            for index, block in enumerate(event.payload["response"]["content"]):
                if block.get("type") != "tool_use":
                    continue
                key = occurrence_key(run_id, event.seq, index)
                calls.append({"occurrence_key": key, "name": block.get("name")})
                result = results.get(key)
                if result is None:
                    errors.append(f"missing result for {key}")
                elif result.payload["tool_index"] != index:
                    errors.append(f"tool index mismatch for {key}")
            decisions.append(
                {
                    "kind": "response",
                    "request_id": request_id,
                    "stop_reason": event.payload["response"]["stop_reason"],
                    "calls": calls,
                }
            )
        elif event.event_type in {"run_completed", "run_stopped", "run_failed"}:
            decisions.append({"kind": "terminal", "type": event.event_type})
    digest = hashlib.sha256(canonical_json(decisions).encode("utf-8")).hexdigest()
    state = store.rebuild_state(run_id)
    return ReplayReport(
        run_id=run_id,
        matches_recording=not errors,
        decision_count=len(decisions),
        decision_hash=digest,
        terminal_status=state.status,
        errors=tuple(errors),
    )
