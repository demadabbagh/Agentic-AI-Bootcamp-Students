"""The response_format each agent's output_pydantic sends must pass OpenAI strict structured outputs.

A live run failed with: 400 Invalid schema for response_format 'DecisionDraft': 'additionalProperties' is required
to be supplied and to be false. CrewAI's OpenAI-compatible provider passes the Pydantic class to the OpenAI SDK
(beta.chat.completions.parse), which keeps an "additionalProperties": true. These tests capture the exact request
body the SDK sends, through an in-memory transport: no network, no LLM.
"""
import json
from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict

from planning.budget_analyst import BudgetDecision
from planning.itinerary_planner import DayAssignment
from planning.revision_agent import RevisionDraft
from planning.verifier_agent import DecisionDraft, _extra_problems

TYPED = ("type", "anyOf", "$ref", "enum", "const")


def sent_response_format(model) -> dict:
    """The response_format of the request the OpenAI SDK sends for `model`, as the provider receives it."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "{}"}}]})

    client = openai.OpenAI(api_key="offline-test", base_url="http://provider.invalid/v1",
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.beta.chat.completions.parse(model="test", messages=[{"role": "user", "content": "x"}],
                                           response_format=model)
    except Exception:
        pass  # "{}" may not parse as the model; the request was already captured
    return captured["body"]["response_format"]


def strict_violations(node, path="$") -> list[str]:
    """OpenAI strict mode: every object closed with every property required, every schema typed."""
    out = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                out.append(f"{path}: additionalProperties is {node.get('additionalProperties', 'missing')!r}")
            if set(node.get("required", [])) != set(node.get("properties", {})):
                out.append(f"{path}: not every property is required")
            out += [f"{path}.properties.{k}: no type" for k, v in node.get("properties", {}).items()
                    if not any(t in v for t in TYPED)]
        if node.get("type") == "array" and not any(t in node.get("items", {}) for t in TYPED):
            out.append(f"{path}.items: no type")
        for key, value in node.items():
            out += strict_violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += strict_violations(value, f"{path}[{i}]")
    return out


@pytest.mark.parametrize("model", [DecisionDraft, RevisionDraft, BudgetDecision, DayAssignment])
def test_the_schema_sent_to_the_provider_is_strict(model):
    response_format = sent_response_format(model)
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == model.__name__
    assert strict_violations(response_format["json_schema"]["schema"]) == []


def test_decision_draft_nested_finding_schema_is_closed_and_typed():
    schema = sent_response_format(DecisionDraft)["json_schema"]["schema"]
    finding = schema["$defs"]["FindingDraft"]
    assert schema["additionalProperties"] is False and finding["additionalProperties"] is False
    assert schema["properties"]["findings"]["items"] == {"$ref": "#/$defs/FindingDraft"}
    assert finding["properties"]["rank"]["type"] == "integer"
    assert {t["type"] for t in finding["properties"]["date"]["anyOf"]} == {"string", "null"}
    assert schema["properties"]["notes"]["items"] == {"type": "string"}


def test_the_draft_still_validates_leniently_so_the_guardrail_can_judge():
    draft = DecisionDraft.model_validate({"findings": [{"finding_id": 7, "rank": "first", "surprise": 1}],
                                          "summary": 3, "bonus": True})
    assert draft.model_extra == {"bonus": True}
    assert draft.findings[0].model_extra == {"surprise": 1}
    assert draft.summary == 3 and draft.findings[0].rank == "first"


def test_unknown_keys_are_still_reported_by_the_guardrail():
    draft = DecisionDraft.model_validate({"findings": [{"finding_id": "E1", "surprise": 1}], "summary": "ok",
                                          "bonus": True})
    problems = _extra_problems("the answer", draft) + _extra_problems("E1", draft.findings[0])
    assert [p.kind for p in problems] == ["shape", "shape"]
    assert any("bonus" in p.text for p in problems) and any("surprise" in p.text for p in problems)


def test_the_check_catches_the_schema_the_live_run_sent():
    class FindingDraft(BaseModel):  # the definitions before the fix
        model_config = ConfigDict(extra="allow")
        rank: Any = None

    class DecisionDraft(BaseModel):
        model_config = ConfigDict(extra="allow")
        findings: list[FindingDraft] = []
        summary: Any = None

    violations = strict_violations(sent_response_format(DecisionDraft)["json_schema"]["schema"])
    assert "$: additionalProperties is True" in violations
    assert any("FindingDraft: additionalProperties is True" in v for v in violations)
    assert any(v.endswith("summary: no type") for v in violations)


def test_revision_draft_nested_choice_schema_is_closed_and_typed_and_validation_stays_lenient():
    schema = sent_response_format(RevisionDraft)["json_schema"]["schema"]
    choice = schema["$defs"]["ChoiceDraft"]
    assert schema["additionalProperties"] is False and choice["additionalProperties"] is False
    assert {k: v["type"] for k, v in choice["properties"].items()} == {
        "decision_id": "string", "alternative_id": "string", "reason": "string"}

    draft = RevisionDraft.model_validate({"choices": [{"decision_id": 1, "extra": "x"}], "bonus": True})
    assert draft.model_extra == {"bonus": True} and draft.choices[0].model_extra == {"extra": "x"}
