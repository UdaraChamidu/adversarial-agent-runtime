"""Small deterministic tokenizer shared by the mock server and agent runtime.

This is deliberately not an imitation of a vendor tokenizer. Its contract is
stability: the same Unicode text always receives the same count on every machine.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import Any


_SPAN_PATTERN = re.compile(r"\s+|\w+|[^\w\s]", flags=re.UNICODE)
_BYTES_PER_TOKEN = 4


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def count_text_tokens(text: str) -> int:
    """Count deterministic token units in text.

    Text is split into whitespace, word, and punctuation spans. Every started
    four UTF-8 bytes in a span count as one token. Empty text costs zero.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return sum(
        max(1, (len(match.group(0).encode("utf-8")) + _BYTES_PER_TOKEN - 1) // 4)
        for match in _SPAN_PATTERN.finditer(text)
    )


def count_tokens(value: Any) -> int:
    """Count a string or canonical JSON-compatible value."""

    if isinstance(value, str):
        return count_text_tokens(value)
    return count_text_tokens(canonical_json(value))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count deterministic mock tokens")
    parser.add_argument("text", nargs="?", help="text to count; stdin when omitted")
    parser.add_argument(
        "--json", action="store_true", help="parse the input as JSON before counting"
    )
    args = parser.parse_args(argv)
    raw = args.text if args.text is not None else sys.stdin.read()
    value = json.loads(raw) if args.json else raw
    print(count_tokens(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
