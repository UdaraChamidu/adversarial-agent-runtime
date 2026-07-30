from __future__ import annotations

import json
import unittest

from evals.cases import catalog
from evals.runner import DEFAULT_BASELINE, compare_results, diff_is_empty


class EvalContractTests(unittest.TestCase):
    def test_catalog_meets_count_adversarial_and_known_failure_requirements(self) -> None:
        cases = catalog()
        self.assertGreaterEqual(len(cases), 12)
        self.assertGreaterEqual(sum(case.adversarial for case in cases), 4)
        self.assertEqual(len({case.id for case in cases}), len(cases))
        baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))["statuses"]
        self.assertEqual(set(baseline), {case.id for case in cases})
        self.assertGreaterEqual(sum(status == "fail" for status in baseline.values()), 2)

    def test_baseline_diff_detects_regression_and_improvement(self) -> None:
        expected = {"pass-case": "pass", "known-gap": "fail"}
        self.assertTrue(diff_is_empty(compare_results(dict(expected), expected)))
        regression = compare_results(
            {"pass-case": "fail", "known-gap": "pass"}, expected
        )
        self.assertEqual(
            set(regression["changed"]), {"pass-case", "known-gap"}
        )
        self.assertFalse(diff_is_empty(regression))


if __name__ == "__main__":
    unittest.main()
