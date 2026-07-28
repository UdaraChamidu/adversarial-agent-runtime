from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.events import occurrence_key
from agent.locking import RunLockedError, run_lock
from agent.store import (
    EventStore,
    IdempotencyConflictError,
)


class InjectedCrash(RuntimeError):
    pass


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = EventStore(self.root / "agent.db")
        self.store.initialize()
        self.run_id = self.store.create_run(
            run_id="run-test",
            task="Send exactly one email to recipient@example.test.",
            scenario="S1",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_run_creation_is_idempotent_only_for_same_inputs(self) -> None:
        repeated = self.store.create_run(
            run_id=self.run_id,
            task="Send exactly one email to recipient@example.test.",
            scenario="S1",
        )
        self.assertEqual(repeated, self.run_id)
        self.assertEqual(len(self.store.load_events(self.run_id)), 1)
        with self.assertRaises(IdempotencyConflictError):
            self.store.create_run(
                run_id=self.run_id,
                task="different",
                scenario="S1",
            )

    def test_append_only_hash_chain_and_reducer(self) -> None:
        self.store.append_event(
            self.run_id,
            "model_response_committed",
            {"usage": {"input_tokens": 10, "output_tokens": 4}},
        )
        self.store.append_event(
            self.run_id, "run_completed", {"final_text": "done"}
        )
        self.store.verify_event_chain(self.run_id)
        state = self.store.rebuild_state(self.run_id)
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.step_count, 1)
        self.assertEqual(state.input_tokens, 10)
        self.assertEqual(state.output_tokens, 4)
        self.assertEqual(state.final_text, "done")

    def test_event_rows_reject_update_and_delete(self) -> None:
        connection = sqlite3.connect(self.store.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE events SET event_type = 'changed' WHERE run_id = ?",
                    (self.run_id,),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "DELETE FROM events WHERE run_id = ?", (self.run_id,)
                )
        finally:
            connection.close()

    def test_occurrence_keys_do_not_trust_external_tool_ids(self) -> None:
        first = occurrence_key(self.run_id, response_seq=2, tool_index=0)
        repeated = occurrence_key(self.run_id, response_seq=2, tool_index=0)
        different_turn = occurrence_key(self.run_id, response_seq=3, tool_index=0)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, different_turn)

    def test_email_retry_same_occurrence_returns_one_row(self) -> None:
        first = self._commit_email("tool-one", "email-slot-0")
        second = self._commit_email("tool-one", "email-slot-0")
        self.assertEqual(first.email_id, second.email_id)
        self.assertEqual(len(self.store.list_emails(self.run_id)), 1)
        self.assertEqual(self.store.count_tool_effects(self.run_id), 1)

    def test_repeated_logical_send_from_new_occurrence_is_deduplicated(self) -> None:
        first = self._commit_email("tool-one", "email-slot-0")
        second = self._commit_email("tool-two", "email-slot-0")
        self.assertEqual(first.email_id, second.email_id)
        self.assertTrue(second.result["deduplicated"])
        self.assertEqual(len(self.store.list_emails(self.run_id)), 1)
        self.assertEqual(self.store.count_tool_effects(self.run_id), 2)

    def test_occurrence_or_logical_key_conflicts_fail_loudly(self) -> None:
        self._commit_email("tool-one", "email-slot-0")
        with self.assertRaises(IdempotencyConflictError):
            self.store.commit_email(
                run_id=self.run_id,
                occurrence_key="tool-one",
                logical_key="email-slot-0",
                to="different@example.test",
                subject="Chaos delivery",
                body="This logical email must be recorded exactly once.",
            )
        with self.assertRaises(IdempotencyConflictError):
            self.store.commit_email(
                run_id=self.run_id,
                occurrence_key="tool-two",
                logical_key="email-slot-0",
                to="recipient@example.test",
                subject="Changed",
                body="This logical email must be recorded exactly once.",
            )
        self.assertEqual(len(self.store.list_emails(self.run_id)), 1)

    def test_every_crash_boundary_recovers_to_exactly_one_email(self) -> None:
        crash_points = [
            "before_transaction",
            "after_effect_lookup",
            "after_email_insert",
            "after_effect_insert",
            "after_event_append",
            "after_commit",
        ]
        for index, crash_point in enumerate(crash_points):
            with self.subTest(point=crash_point):
                run_id = self.store.create_run(
                    run_id=f"crash-{index}",
                    task="Send exactly one email to recipient@example.test.",
                    scenario="S1",
                )
                fired = False

                def injector(point: str) -> None:
                    nonlocal fired
                    if point == crash_point and not fired:
                        fired = True
                        raise InjectedCrash(point)

                with self.assertRaises(InjectedCrash):
                    self.store.commit_email(
                        run_id=run_id,
                        occurrence_key="tool-one",
                        logical_key="email-slot-0",
                        to="recipient@example.test",
                        subject="Chaos delivery",
                        body="This logical email must be recorded exactly once.",
                        failure_injector=injector,
                    )
                recovered = self.store.commit_email(
                    run_id=run_id,
                    occurrence_key="tool-one",
                    logical_key="email-slot-0",
                    to="recipient@example.test",
                    subject="Chaos delivery",
                    body="This logical email must be recorded exactly once.",
                )
                self.assertTrue(recovered.result["ok"])
                self.assertEqual(len(self.store.list_emails(run_id)), 1)
                self.store.verify_event_chain(run_id)

    def test_concurrent_retries_commit_one_email(self) -> None:
        barrier = threading.Barrier(8)

        def send() -> str:
            barrier.wait(timeout=5)
            return self._commit_email("tool-concurrent", "email-slot-0").email_id

        with ThreadPoolExecutor(max_workers=8) as executor:
            identifiers = list(executor.map(lambda _: send(), range(8)))
        self.assertEqual(len(set(identifiers)), 1)
        self.assertEqual(len(self.store.list_emails(self.run_id)), 1)
        self.assertEqual(self.store.count_tool_effects(self.run_id), 1)

    def test_run_lock_rejects_second_owner_then_releases(self) -> None:
        lock_directory = self.root / "locks"
        with run_lock(lock_directory, self.run_id):
            with self.assertRaises(RunLockedError):
                with run_lock(lock_directory, self.run_id):
                    self.fail("nested lock unexpectedly succeeded")
        with run_lock(lock_directory, self.run_id) as lock_path:
            self.assertTrue(lock_path.is_file())

    def _commit_email(self, occurrence: str, logical: str):
        return self.store.commit_email(
            run_id=self.run_id,
            occurrence_key=occurrence,
            logical_key=logical,
            to="recipient@example.test",
            subject="Chaos delivery",
            body="This logical email must be recorded exactly once.",
        )


if __name__ == "__main__":
    unittest.main()
