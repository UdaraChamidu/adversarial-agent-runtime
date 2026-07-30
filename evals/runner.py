"""Eval execution, stored-baseline comparison, and report generation."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from evals.cases import CaseResult, EvalEnvironment, catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "evals" / "baseline" / "results.json"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "latest.json"


def compare_results(
    actual: dict[str, str], expected: dict[str, str]
) -> dict[str, object]:
    return {
        "added": sorted(set(actual) - set(expected)),
        "removed": sorted(set(expected) - set(actual)),
        "changed": {
            case_id: {"expected": expected[case_id], "actual": actual[case_id]}
            for case_id in sorted(set(actual) & set(expected))
            if actual[case_id] != expected[case_id]
        },
    }


def diff_is_empty(diff: dict[str, object]) -> bool:
    return not diff["added"] and not diff["removed"] and not diff["changed"]


def run_evals() -> dict[str, object]:
    environment = EvalEnvironment()
    results: list[dict[str, object]] = []
    try:
        for case in catalog():
            started = time.monotonic()
            try:
                result = case.evaluate(environment)
            except Exception as exc:
                result = CaseResult(
                    False,
                    {"uncaught_error": f"{type(exc).__name__}: {exc}"},
                )
            results.append(
                {
                    "id": case.id,
                    "category": case.category,
                    "adversarial": case.adversarial,
                    "description": case.description,
                    "status": "pass" if result.passed else "fail",
                    "duration_seconds": round(time.monotonic() - started, 4),
                    "details": result.details,
                }
            )
    finally:
        environment.close()
    passed = sum(result["status"] == "pass" for result in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)

    report = run_evals()
    actual = {
        result["id"]: result["status"]
        for result in report["results"]
    }
    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps({"statuses": actual}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if not args.baseline.is_file():
        print(f"baseline is missing: {args.baseline}")
        return 2
    expected = json.loads(args.baseline.read_text(encoding="utf-8"))["statuses"]
    diff = compare_results(actual, expected)
    report["baseline_diff"] = diff
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for result in report["results"]:
        marker = "PASS" if result["status"] == "pass" else "FAIL"
        print(f"{marker} {result['id']}: {result['description']}")
    print(
        f"Pass rate: {report['passed']}/{report['total']} "
        f"({report['pass_rate'] * 100:.1f}%)"
    )
    print("Baseline diff: " + json.dumps(diff, sort_keys=True))
    return 0 if diff_is_empty(diff) else 1


if __name__ == "__main__":
    raise SystemExit(main())
