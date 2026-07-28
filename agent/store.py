"""SQLite event store and atomic simulated side-effect boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from agent.events import (
    GENESIS_HASH,
    Event,
    calculate_event_hash,
    event_id,
)
from agent.state import RunState, rebuild_state
from mockllm.tokenizer import canonical_json


SCHEMA_VERSION = 1


class StoreError(RuntimeError):
    pass


class RunNotFoundError(StoreError):
    pass


class IdempotencyConflictError(StoreError):
    pass


class EventChainError(StoreError):
    pass


FailureInjector = Callable[[str], None]


@dataclass(frozen=True)
class EmailEffect:
    email_id: str
    run_id: str
    occurrence_key: str
    logical_key: str
    to: str
    subject: str
    body: str
    result: dict[str, Any]
    created_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _input_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class EventStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_seq INTEGER NOT NULL,
                    last_event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TRIGGER IF NOT EXISTS events_no_update
                BEFORE UPDATE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS events_no_delete
                BEFORE DELETE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;

                CREATE TABLE IF NOT EXISTS tool_effects (
                    run_id TEXT NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, occurrence_key),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS emails (
                    email_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (run_id, occurrence_key),
                    UNIQUE (run_id, logical_key),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS events_by_type
                ON events(run_id, event_type, seq);
                """
            )
            row = connection.execute("SELECT version FROM schema_meta").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif row["version"] != SCHEMA_VERSION:
                raise StoreError(
                    f"database schema {row['version']} is not supported "
                    f"(expected {SCHEMA_VERSION})"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.rollback()

    def create_run(
        self,
        *,
        task: str,
        scenario: str,
        run_id: str | None = None,
    ) -> str:
        if not task.strip():
            raise ValueError("task must not be empty")
        if not scenario.strip():
            raise ValueError("scenario must not be empty")
        selected_id = run_id or uuid.uuid4().hex
        now = _utc_now()
        with self._connect() as connection:
            self._begin(connection)
            try:
                existing = connection.execute(
                    "SELECT task, scenario FROM runs WHERE run_id = ?",
                    (selected_id,),
                ).fetchone()
                if existing is not None:
                    if existing["task"] != task or existing["scenario"] != scenario:
                        raise IdempotencyConflictError(
                            f"run {selected_id!r} already exists with different inputs"
                        )
                    connection.commit()
                    return selected_id

                connection.execute(
                    """
                    INSERT INTO runs(
                        run_id, task, scenario, status, next_seq,
                        last_event_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, 'running', 1, ?, ?, ?)
                    """,
                    (selected_id, task, scenario, GENESIS_HASH, now, now),
                )
                self._append_event_in_transaction(
                    connection,
                    selected_id,
                    "run_created",
                    {"task": task, "scenario": scenario},
                )
                connection.commit()
                return selected_id
            except BaseException:
                self._rollback(connection)
                raise

    def append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> Event:
        if not event_type:
            raise ValueError("event_type must not be empty")
        canonical_json(payload)
        with self._connect() as connection:
            self._begin(connection)
            try:
                event = self._append_event_in_transaction(
                    connection, run_id, event_type, payload
                )
                status = {
                    "run_completed": "completed",
                    "run_stopped": "stopped",
                    "run_failed": "failed",
                }.get(event_type)
                if status is not None:
                    connection.execute(
                        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                        (status, _utc_now(), run_id),
                    )
                connection.commit()
                return event
            except BaseException:
                self._rollback(connection)
                raise

    def _append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> Event:
        run = connection.execute(
            "SELECT next_seq, last_event_hash FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise RunNotFoundError(run_id)
        seq = int(run["next_seq"])
        previous = str(run["last_event_hash"])
        digest = calculate_event_hash(
            run_id=run_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            prev_hash=previous,
        )
        created_at = _utc_now()
        identifier = event_id(run_id, seq, digest)
        payload_json = canonical_json(payload)
        connection.execute(
            """
            INSERT INTO events(
                run_id, seq, event_id, event_type, payload_json,
                prev_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                identifier,
                event_type,
                payload_json,
                previous,
                digest,
                created_at,
            ),
        )
        connection.execute(
            """
            UPDATE runs
            SET next_seq = ?, last_event_hash = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (seq + 1, digest, created_at, run_id),
        )
        return Event(
            run_id=run_id,
            seq=seq,
            event_id=identifier,
            event_type=event_type,
            payload=payload,
            prev_hash=previous,
            event_hash=digest,
            created_at=created_at,
        )

    def load_events(self, run_id: str) -> list[Event]:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RunNotFoundError(run_id)
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [
            Event(
                run_id=row["run_id"],
                seq=int(row["seq"]),
                event_id=row["event_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                prev_hash=row["prev_hash"],
                event_hash=row["event_hash"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def verify_event_chain(self, run_id: str) -> None:
        previous = GENESIS_HASH
        for expected_seq, event in enumerate(self.load_events(run_id), start=1):
            if event.seq != expected_seq:
                raise EventChainError(
                    f"expected event sequence {expected_seq}, found {event.seq}"
                )
            if event.prev_hash != previous:
                raise EventChainError(f"event {event.seq} has an invalid previous hash")
            expected_hash = calculate_event_hash(
                run_id=event.run_id,
                seq=event.seq,
                event_type=event.event_type,
                payload=event.payload,
                prev_hash=event.prev_hash,
            )
            if event.event_hash != expected_hash:
                raise EventChainError(f"event {event.seq} hash does not match its data")
            if event.event_id != event_id(event.run_id, event.seq, event.event_hash):
                raise EventChainError(f"event {event.seq} id is invalid")
            previous = event.event_hash

    def rebuild_state(self, run_id: str) -> RunState:
        return rebuild_state(run_id, self.load_events(run_id))

    def commit_email(
        self,
        *,
        run_id: str,
        occurrence_key: str,
        logical_key: str,
        to: str,
        subject: str,
        body: str,
        failure_injector: FailureInjector | None = None,
    ) -> EmailEffect:
        if not all(
            isinstance(value, str) and value
            for value in (occurrence_key, logical_key, to, subject, body)
        ):
            raise ValueError("email effect fields must be non-empty strings")
        email_input = {"to": to, "subject": subject, "body": body}
        input_digest = _input_hash(email_input)
        inject = failure_injector or (lambda _point: None)
        inject("before_transaction")

        with self._connect() as connection:
            self._begin(connection)
            try:
                existing_effect = connection.execute(
                    """
                    SELECT * FROM tool_effects
                    WHERE run_id = ? AND occurrence_key = ?
                    """,
                    (run_id, occurrence_key),
                ).fetchone()
                inject("after_effect_lookup")
                if existing_effect is not None:
                    if (
                        existing_effect["tool_name"] != "send_email"
                        or existing_effect["logical_key"] != logical_key
                        or existing_effect["input_hash"] != input_digest
                    ):
                        raise IdempotencyConflictError(
                            "tool occurrence was reused with different email inputs"
                        )
                    effect = self._email_effect_for_occurrence(
                        connection, run_id, occurrence_key
                    )
                    connection.commit()
                    inject("after_commit")
                    return effect

                existing_email = connection.execute(
                    """
                    SELECT * FROM emails
                    WHERE run_id = ? AND logical_key = ?
                    """,
                    (run_id, logical_key),
                ).fetchone()
                if existing_email is not None and existing_email["input_hash"] != input_digest:
                    raise IdempotencyConflictError(
                        "logical email key was reused with different content"
                    )

                now = _utc_now()
                deduplicated = existing_email is not None
                if existing_email is None:
                    email_identifier = (
                        "eml_"
                        + hashlib.sha256(
                            f"{run_id}:{logical_key}".encode("utf-8")
                        ).hexdigest()[:32]
                    )
                    connection.execute(
                        """
                        INSERT INTO emails(
                            email_id, run_id, occurrence_key, logical_key,
                            to_address, subject, body, input_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            email_identifier,
                            run_id,
                            occurrence_key,
                            logical_key,
                            to,
                            subject,
                            body,
                            input_digest,
                            now,
                        ),
                    )
                    inject("after_email_insert")
                else:
                    email_identifier = str(existing_email["email_id"])

                result = {
                    "ok": True,
                    "email_id": email_identifier,
                    "delivery": "simulated",
                    "deduplicated": deduplicated,
                }
                connection.execute(
                    """
                    INSERT INTO tool_effects(
                        run_id, occurrence_key, tool_name, logical_key,
                        input_hash, result_json, created_at
                    ) VALUES (?, ?, 'send_email', ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        occurrence_key,
                        logical_key,
                        input_digest,
                        canonical_json(result),
                        now,
                    ),
                )
                inject("after_effect_insert")
                self._append_event_in_transaction(
                    connection,
                    run_id,
                    "email_effect_committed",
                    {
                        "occurrence_key": occurrence_key,
                        "logical_key": logical_key,
                        "email_id": email_identifier,
                        "to": to,
                        "subject": subject,
                        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                        "deduplicated": deduplicated,
                    },
                )
                inject("after_event_append")
                connection.commit()
                inject("after_commit")
                return EmailEffect(
                    email_id=email_identifier,
                    run_id=run_id,
                    occurrence_key=occurrence_key,
                    logical_key=logical_key,
                    to=to,
                    subject=subject,
                    body=body,
                    result=result,
                    created_at=now,
                )
            except BaseException:
                self._rollback(connection)
                raise

    @staticmethod
    def _email_effect_for_occurrence(
        connection: sqlite3.Connection, run_id: str, occurrence_key: str
    ) -> EmailEffect:
        row = connection.execute(
            """
            SELECT
                e.email_id, e.run_id, ? AS occurrence_key, e.logical_key,
                e.to_address, e.subject, e.body, e.created_at,
                t.result_json
            FROM tool_effects AS t
            JOIN emails AS e
              ON e.run_id = t.run_id AND e.logical_key = t.logical_key
            WHERE t.run_id = ? AND t.occurrence_key = ?
            """,
            (occurrence_key, run_id, occurrence_key),
        ).fetchone()
        if row is None:
            raise StoreError("email effect exists without its email row")
        return EmailEffect(
            email_id=row["email_id"],
            run_id=row["run_id"],
            occurrence_key=row["occurrence_key"],
            logical_key=row["logical_key"],
            to=row["to_address"],
            subject=row["subject"],
            body=row["body"],
            result=json.loads(row["result_json"]),
            created_at=row["created_at"],
        )

    def list_emails(self, run_id: str) -> list[EmailEffect]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, t.result_json
                FROM emails AS e
                JOIN tool_effects AS t
                  ON t.run_id = e.run_id
                 AND t.occurrence_key = e.occurrence_key
                WHERE e.run_id = ?
                ORDER BY e.created_at, e.email_id
                """,
                (run_id,),
            ).fetchall()
        return [
            EmailEffect(
                email_id=row["email_id"],
                run_id=row["run_id"],
                occurrence_key=row["occurrence_key"],
                logical_key=row["logical_key"],
                to=row["to_address"],
                subject=row["subject"],
                body=row["body"],
                result=json.loads(row["result_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def count_tool_effects(self, run_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM tool_effects WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return int(row["count"])
