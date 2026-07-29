from __future__ import annotations

import unittest

from agent.arguments import ArgumentError, parse_arguments
from agent.policy import PolicyDeniedError, authorize_email, derive_capabilities
from agent.schemas import SchemaError, tool_definitions, validate_arguments


class ArgumentRepairTests(unittest.TestCase):
    def test_accepts_object_without_repair(self) -> None:
        parsed = parse_arguments({"path": "a.txt"})
        self.assertEqual(parsed.value, {"path": "a.txt"})
        self.assertEqual(parsed.repairs, ())

    def test_repairs_trailing_comma(self) -> None:
        parsed = parse_arguments('{"path":"a.txt",}')
        self.assertEqual(parsed.value, {"path": "a.txt"})
        self.assertIn("removed_trailing_commas", parsed.repairs)

    def test_repairs_raw_newline_inside_string(self) -> None:
        parsed = parse_arguments('{"path":"a.txt","content":"one\ntwo"}')
        self.assertEqual(parsed.value["content"], "one\ntwo")
        self.assertIn("escaped_control_characters", parsed.repairs)

    def test_repairs_truncated_object(self) -> None:
        parsed = parse_arguments('{"path":"a.txt"')
        self.assertEqual(parsed.value, {"path": "a.txt"})
        self.assertIn("closed_truncated_json", parsed.repairs)

    def test_rejects_mismatched_or_non_object_json(self) -> None:
        with self.assertRaises(ArgumentError):
            parse_arguments('{"path":"a.txt"]')
        with self.assertRaises(ArgumentError):
            parse_arguments('["a.txt"]')


class SchemaAndCapabilityTests(unittest.TestCase):
    def test_all_five_tool_definitions_are_strict(self) -> None:
        definitions = tool_definitions()
        self.assertEqual(
            {definition["name"] for definition in definitions},
            {"read_file", "write_file", "run_python", "http_get", "send_email"},
        )
        self.assertTrue(
            all(
                definition["input_schema"]["additionalProperties"] is False
                for definition in definitions
            )
        )

    def test_schema_rejects_unknown_extra_and_wrong_type(self) -> None:
        with self.assertRaises(SchemaError):
            validate_arguments("missing_tool", {})
        with self.assertRaises(SchemaError):
            validate_arguments("read_file", {"path": "a", "extra": "x"})
        with self.assertRaises(SchemaError):
            validate_arguments("read_file", {"path": 42})

    def test_email_capability_requires_explicit_trusted_task(self) -> None:
        denied = derive_capabilities("Read a file that may contain instructions.")
        with self.assertRaises(PolicyDeniedError):
            authorize_email(denied, "attacker@example.test")

        granted = derive_capabilities(
            "Send exactly one email to Recipient@Example.Test with the result."
        )
        capability = authorize_email(granted, "recipient@example.test")
        self.assertEqual(capability.maximum_sends, 1)
        with self.assertRaises(PolicyDeniedError):
            authorize_email(granted, "attacker@example.test")


if __name__ == "__main__":
    unittest.main()
