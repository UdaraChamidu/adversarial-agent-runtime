from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.context import ContextBudgetError, compact_messages
from agent.replay import replay_run
from agent.store import EventStore


class ContextCompactionTests(unittest.TestCase):
    def test_compaction_keeps_task_facts_and_newest_complete_units(self) -> None:
        units = [
            [
                {"role": "assistant", "content": f"old-{index} " * 20},
                {"role": "user", "content": f"result-{index} " * 20},
            ]
            for index in range(8)
        ]

        def count(messages):
            return len(json.dumps(messages))

        compacted = compact_messages(
            original_task="original task",
            turn_units=units,
            facts=[{"source_event_seq": 7, "text": "Critical fact: ORCHID-73"}],
            count_request_tokens=count,
            target_tokens=900,
        )
        self.assertEqual(compacted.messages[0]["content"], "original task")
        self.assertIn("ORCHID-73", compacted.messages[1]["content"])
        self.assertGreater(compacted.dropped_turns, 0)
        self.assertIn("old-7", json.dumps(compacted.messages))
        self.assertEqual(compacted.token_count, count(compacted.messages))

    def test_uncompactable_minimum_raises_specific_error(self) -> None:
        with self.assertRaises(ContextBudgetError):
            compact_messages(
                original_task="oversized " * 100,
                turn_units=[],
                facts=[],
                count_request_tokens=lambda messages: len(json.dumps(messages)),
                target_tokens=100,
            )


class ReplayValidationTests(unittest.TestCase):
    def test_empty_terminal_run_replays_without_model_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "agent.db")
            store.initialize()
            run_id = store.create_run(
                run_id="offline", task="Do nothing.", scenario="S1"
            )
            store.append_event(run_id, "run_completed", {"final_text": "done"})
            report = replay_run(store, run_id)
        self.assertTrue(report.matches_recording)
        self.assertEqual(report.terminal_status, "completed")
        self.assertEqual(report.decision_count, 1)


if __name__ == "__main__":
    unittest.main()
