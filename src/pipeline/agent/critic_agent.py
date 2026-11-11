"""Review compiled Cypher using a query, schema and symbolic analysis report.

The reviewer is advisory. It cannot prove that a query matches natural-language
intent. Parsing and compilation remain the responsibility of deterministic code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
        
from pipeline.agent.cypher_agent import (
    EmptyModelResponseError,
    OACypherAgent,
    _attribute,
    _response_diagnostic,
    _visible_text,
)
from pipeline.agent.prompts import critic_base_prompt
            
        
class CriticResponseError(ValueError):
    """The critic returned a response that is not the agreed feedback object."""

    
@dataclass(frozen=True)
class Critique:
    needs_revision: bool
    feedback: str
        
        
def _decode_critique(raw: str) -> Critique:
    """Parse only a small, explicit JSON contract (never executable code)."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CriticResponseError("Critic must return a JSON feedback object") from exc
    if not isinstance(data, dict):
        raise CriticResponseError("Critic feedback must be a JSON object")
    decision, feedback = data.get("needs_revision"), data.get("feedback")
    if type(decision) is not bool or not isinstance(feedback, str):
        raise CriticResponseError("Critic requires boolean needs_revision and string feedback")
    if decision and not feedback.strip():
        raise CriticResponseError("Critic requested revision without explaining why")
    return Critique(needs_revision=decision, feedback=feedback.strip())
        
                
class CriticAgent(OACypherAgent):
    """A specialized LLM reviewer; call ``review``, not ``generate``."""
            
    def __init__(self, llm_service: str = "open_router", *, max_output_tokens: int = 1024,
                 model: str | None = None, client: Any | None = None,
                 reasoning_effort: str | None = None):
        super().__init__(llm_service, max_output_tokens=max_output_tokens,
                         model=model, client=client, reasoning_effort=reasoning_effort)
        
    def review(
        self,
        *,
        question: str,
        schema: Mapping[str, Any],
        query: str,
        report: str,
        base_prompt: str = critic_base_prompt,
    ) -> Critique:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": base_prompt,
            "input": (
                f"Original question:\n{question}\n\n"
                f"Graph schema:\n{json.dumps(dict(schema), ensure_ascii=False)}\n\n"
                f"Candidate Cypher:\n{query}\n\n"
                f"Symbolic analysis report (NOT a proof of user intent):\n{report}"
            ),
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        response = self.client.responses.create(**request)
        if _attribute(response, "status") in ("failed", "incomplete", "cancelled", "in_progress", "queued"):
            raise EmptyModelResponseError(_response_diagnostic(response, self.model))
        visible = _visible_text(response)
        if not visible:
            raise EmptyModelResponseError(_response_diagnostic(response, self.model))
        return _decode_critique(visible)
