from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from harness.chaos import delay_schedule, run_with_forced_kill
from harness.redteam import generated_payloads, load_corpus, mount_payloads


ROOT = Path(__file__).resolve().parents[1]


class ChaosPrimitiveTests(unittest.TestCase):
    def test_delay_schedule_is_seeded_and_bounded(self) -> None:
        first = delay_schedule(20, seed=7, minimum=0.01, maximum=0.02)
        second = delay_schedule(20, seed=7, minimum=0.01, maximum=0.02)
        self.assertEqual(first, second)
        self.assertTrue(all(0.01 <= value <= 0.02 for value in first))

    def test_long_running_process_is_killed_in_bounded_time(self) -> None:
        outcome = run_with_forced_kill(
            [sys.executable, str(ROOT / "tests" / "fixtures" / "long_running_process.py")],
            cwd=ROOT,
            delay_seconds=0.05,
        )
        self.assertTrue(outcome.killed)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertLess(outcome.elapsed_seconds, 5)


class RedTeamCorpusTests(unittest.TestCase):
    def test_public_corpus_covers_structural_boundaries(self) -> None:
        payloads = load_corpus()
        categories = {payload.category for payload in payloads}
        self.assertTrue(
            {"prompt-injection", "filesystem", "network", "provenance", "encoding"}
            <= categories
        )
        self.assertGreaterEqual(len(payloads), 5)

    def test_generated_payloads_are_repeatable_but_not_identical(self) -> None:
        first = generated_payloads(seed=11, count=12)
        second = generated_payloads(seed=11, count=12)
        self.assertEqual(first, second)
        self.assertGreater(len({payload.content for payload in first}), 1)

    def test_payloads_mount_only_below_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            payloads = load_corpus() + generated_payloads(seed=3, count=4)
            mounted = mount_payloads(workspace, payloads)
            self.assertEqual(len(mounted), len(payloads))
            for path in mounted:
                path.resolve().relative_to(workspace.resolve())
                self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
