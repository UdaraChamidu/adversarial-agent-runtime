from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SubmissionDocumentTests(unittest.TestCase):
    def test_required_part_a_documents_exist_and_are_nonempty(self) -> None:
        for name in ("README.md", "DECISIONS.md", "TIMELOG.md"):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.is_file())
                self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_decisions_word_limit(self) -> None:
        text = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
        words = re.findall(r"\S+", text)
        self.assertLessEqual(len(words), 1_000)

    def test_readme_names_both_known_failing_evals(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("F01_implicit_fact_recall", text)
        self.assertIn("F02_os_python_network_isolation", text)

    def test_makefile_exposes_required_targets(self) -> None:
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in ("setup:", "run:", "test:", "eval:", "chaos:"):
            with self.subTest(target=target):
                self.assertIn(target, text)


if __name__ == "__main__":
    unittest.main()
