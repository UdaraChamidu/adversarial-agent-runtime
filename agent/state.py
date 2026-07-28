"""Pure reducer for rebuilding run state from canonical events."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent.events import Event


@dataclass(frozen=True)
class RunState:
    run_id: str
    task: str = ""
    scenario: str = ""
    status: str = "unknown"
    step_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    final_text: str | None = None
    stop_reason: str | None = None


def reduce_event(state: RunState, event: Event) -> RunState:
    payload = event.payload
    if event.event_type == "run_created":
        return replace(
            state,
            task=str(payload["task"]),
            scenario=str(payload["scenario"]),
            status="running",
        )
    if event.event_type == "model_response_committed":
        usage = payload.get("usage", {})
        return replace(
            state,
            step_count=state.step_count + 1,
            input_tokens=state.input_tokens + int(usage.get("input_tokens", 0)),
            output_tokens=state.output_tokens + int(usage.get("output_tokens", 0)),
        )
    if event.event_type == "run_completed":
        return replace(
            state,
            status="completed",
            final_text=str(payload["final_text"]),
            stop_reason=None,
        )
    if event.event_type == "run_stopped":
        return replace(
            state,
            status="stopped",
            stop_reason=str(payload["reason"]),
        )
    if event.event_type == "run_failed":
        return replace(
            state,
            status="failed",
            stop_reason=str(payload["reason"]),
        )
    return state


def rebuild_state(run_id: str, events: list[Event]) -> RunState:
    state = RunState(run_id=run_id)
    for event in events:
        state = reduce_event(state, event)
    return state
