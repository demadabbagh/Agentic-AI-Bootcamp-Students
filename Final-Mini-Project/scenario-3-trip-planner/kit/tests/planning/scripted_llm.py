"""Test doubles for the real CrewAI agent loop: no network, no cost. Used by the agent tests only.

ScriptedLLM is a crewai BaseLLM that replays fixed replies and records every prompt it was sent, so a test
runs the genuine Agent, Task, tools and guardrail. NativeScriptedLLM answers like a model with native
function calling (the path the live OpenRouter runs used); ScriptedLLM uses the ReAct text protocol.
"""
import json
from typing import Any

from crewai.llms.base_llm import BaseLLM


class ScriptedLLM(BaseLLM):
    """Replays fixed replies and records every prompt: the real CrewAI agent loop, no network, no cost."""
    replies: list[Any] = []  # text answers, or a list of native tool calls
    prompts: list[Any] = []

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None):
        self.prompts.append(list(messages) if isinstance(messages, list) else messages)  # a snapshot: CrewAI
        if not self.replies:                                                             # keeps appending
            raise RuntimeError("ScriptedLLM has no reply left")
        return self.replies.pop(0)

    def supports_function_calling(self) -> bool:
        return False  # the ReAct text protocol: Action / Action Input / Final Answer


class NativeScriptedLLM(ScriptedLLM):
    """Like the live OpenRouter runs: native function calling (a tool call comes back as a list of calls)."""
    def supports_function_calling(self) -> bool:
        return True


def text(messages) -> str:
    return messages if isinstance(messages, str) else "\n".join(str(m.get("content")) for m in messages)


def by_role(messages, role) -> str:
    return "\n".join(str(m.get("content")) for m in messages if m.get("role") == role)


def native_tool_call(name: str, arguments: dict, call_id: str = "call_1") -> list[dict]:
    return [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]


def react_action(name: str, arguments: dict, thought: str = "I check this in Python.") -> str:
    return f"Thought: {thought}\nAction: {name}\nAction Input: {json.dumps(arguments)}"


def react_final(model) -> str:
    return f"Thought: I have decided.\nFinal Answer: {model.model_dump_json()}"
