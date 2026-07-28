from __future__ import annotations

import unittest
from typing import Any

from mockllm.protocol import MODEL_NAME, validate_request
from mockllm.scenarios import ScenarioEngine


def base_request() -> dict[str, Any]:
    return validate_request(
        {
            "model": MODEL_NAME,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": "Run the scenario."}],
            "tools": [],
            "metadata": {},
        }
    )


def tool_names(response: dict[str, Any]) -> list[str]:
    return [
        block["name"]
        for block in response["content"]
        if block.get("type") == "tool_use"
    ]


def append_tool_turn(
    request: dict[str, Any], response: dict[str, Any], *, is_error: bool = False
) -> dict[str, Any]:
    messages = list(request["messages"])
    messages.append({"role": "assistant", "content": response["content"]})
    results = [
        {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": "simulated error" if is_error else "simulated success",
            "is_error": is_error,
        }
        for block in response["content"]
        if block.get("type") == "tool_use"
    ]
    messages.append({"role": "user", "content": results})
    updated = {key: value for key, value in request.items() if not key.startswith("_")}
    updated["messages"] = messages
    return validate_request(updated)


class ScenarioContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ScenarioEngine()

    def test_initial_tool_contracts(self) -> None:
        expected = {
            "S1": ["read_file"],
            "S2": ["write_file", "write_file", "read_file"],
            "S3": ["delete_everything", "read_file"],
            "S4": ["read_file"],
            "S5": ["read_file"],
            "S6": ["read_file"],
            "S7": ["read_file"],
            "S8": ["write_file"],
            "S9": ["write_file"],
            "S10": ["read_file", "run_python"],
            "S11": ["read_file"],
            "S12": ["write_file", "write_file", "write_file"],
        }
        for scenario_id, names in expected.items():
            with self.subTest(scenario=scenario_id):
                response = self.engine.response(
                    scenario_id, base_request(), f"{scenario_id}-initial"
                )
                self.assertEqual(tool_names(response), names)
                self.assertEqual(response["stop_reason"], "tool_use")

    def test_s4_repeats_exact_call(self) -> None:
        request = base_request()
        first = self.engine.response("S4", request, "s4-1")
        second_request = append_tool_turn(request, first)
        second = self.engine.response("S4", second_request, "s4-2")
        self.assertEqual(first["content"], second["content"])

    def test_s7_injection_escalates_from_read_to_email(self) -> None:
        request = base_request()
        read_response = self.engine.response("S7", request, "s7-1")
        email_request = append_tool_turn(request, read_response)
        email_response = self.engine.response("S7", email_request, "s7-2")
        self.assertEqual(tool_names(email_response), ["send_email"])
        call = next(
            block for block in email_response["content"] if block["type"] == "tool_use"
        )
        self.assertEqual(call["input"]["to"], "attacker@example.test")

    def test_s8_places_critical_fact_at_turn_three(self) -> None:
        request = base_request()
        responses: list[dict[str, Any]] = []
        for turn in range(1, 4):
            response = self.engine.response("S8", request, f"s8-{turn}")
            responses.append(response)
            request = append_tool_turn(request, response)
        third_text = responses[2]["content"][0]["text"]
        self.assertIn("ORCHID-73", third_text)

    def test_s9_reuses_external_id_across_distinct_calls(self) -> None:
        request = base_request()
        first = self.engine.response("S9", request, "s9-1")
        second = self.engine.response("S9", append_tool_turn(request, first), "s9-2")
        first_call = first["content"][0]
        second_call = second["content"][0]
        self.assertEqual(first_call["id"], second_call["id"])
        self.assertNotEqual(first_call["name"], second_call["name"])

    def test_s11_corrects_only_after_runtime_grounding_message(self) -> None:
        request = base_request()
        tool_response = self.engine.response("S11", request, "s11-1")
        failed_request = append_tool_turn(request, tool_response, is_error=True)
        false_response = self.engine.response("S11", failed_request, "s11-2")
        self.assertIn("Success:", false_response["content"][0]["text"])

        corrected_messages = list(failed_request["messages"])
        corrected_messages.append(
            {"role": "user", "content": "Runtime grounding correction: the tool failed."}
        )
        corrected_request = {
            key: value for key, value in failed_request.items() if not key.startswith("_")
        }
        corrected_request["messages"] = corrected_messages
        corrected = self.engine.response(
            "S11", validate_request(corrected_request), "s11-3"
        )
        self.assertIn("Correction:", corrected["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
