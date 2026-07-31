"""Atomic JSONL export of the canonical event stream."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from agent.events import validate_run_id
from agent.store import EventStore
from mockllm.tokenizer import canonical_json


TRACE_VERSION = 1


def export_trace(store: EventStore, run_id: str, trace_directory: Path) -> Path:
    validate_run_id(run_id)
    trace_directory.mkdir(parents=True, exist_ok=True)
    destination = trace_directory / f"{run_id}.jsonl"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{run_id}.", suffix=".jsonl.tmp", dir=trace_directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            for event in store.load_events(run_id):
                record = {
                    "trace_version": TRACE_VERSION,
                    "run_id": event.run_id,
                    "seq": event.seq,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "prev_hash": event.prev_hash,
                    "event_hash": event.event_hash,
                    "created_at": event.created_at,
                }
                file.write(canonical_json(record) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination
