from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from mockllm.tokenizer import canonical_json, count_text_tokens, count_tokens


ROOT = Path(__file__).resolve().parents[1]


class TokenizerTests(unittest.TestCase):
    def test_fixed_text_vectors(self) -> None:
        vectors = {
            "": 0,
            "hello": 2,
            "hello world": 5,
            "a  b": 3,
            "abcdefgh": 2,
            "🚀": 1,
            "é": 1,
        }
        for text, expected in vectors.items():
            with self.subTest(text=text):
                self.assertEqual(count_text_tokens(text), expected)

    def test_json_count_is_key_order_independent(self) -> None:
        left = {"z": [1, 2], "a": "é"}
        right = {"a": "é", "z": [1, 2]}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(count_tokens(left), count_tokens(right))

    def test_nan_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json({"unsafe": float("nan")})

    def test_cli_matches_library(self) -> None:
        value = {"messages": [{"role": "user", "content": "hello"}]}
        raw = json.dumps(value)
        result = subprocess.run(
            [sys.executable, "-m", "mockllm.tokenizer", "--json", raw],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(int(result.stdout), count_tokens(value))


if __name__ == "__main__":
    unittest.main()
