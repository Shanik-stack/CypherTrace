"""Generate Cypher using an OpenAI-compatible Responses API.

A successful HTTP response can have no visible output (for example, when a
reasoning model exhausts its output-token budget). Do not treat that case as a
successful query, and preserve the information needed to diagnose it.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv


class EmptyModelResponseError(RuntimeError):
    """The provider returned no complete, visible Cypher response."""


def _attribute(obj: Any, name: str, default: Any = None) -> Any:
    """Read both the SDK's typed objects and dictionary responses in tests."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _visible_text(response: Any) -> str:
    """Extract only assistant output text; never use reasoning as Cypher."""
    text = _attribute(response, "output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    chunks = []
    for item in _attribute(response, "output", ()) or ():
        if _attribute(item, "type") != "message" or _attribute(item, "role") != "assistant":
            continue
        for part in _attribute(item, "content", ()) or ():
            if _attribute(part, "type") == "output_text":
                part_text = _attribute(part, "text")
                if isinstance(part_text, str):
                    chunks.append(part_text)
    return "".join(chunks).strip()


def _response_diagnostic(response: Any, model: str) -> str:
    status = _attribute(response, "status", "unknown")
    reason = _attribute(_attribute(response, "incomplete_details"), "reason")
    error = _attribute(response, "error")
    usage = _attribute(response, "usage")
    output_tokens = _attribute(usage, "output_tokens")
    reasoning_tokens = _attribute(_attribute(usage, "output_tokens_details"), "reasoning_tokens")
    output_types = [_attribute(item, "type") for item in _attribute(response, "output", ()) or ()]

    parts = [f"Model {model!r} returned no complete visible Cypher (status={status!r})"]
    if reason:
        parts.append(f"incomplete_reason={reason!r}")
    if output_tokens is not None:
        parts.append(f"output_tokens={output_tokens}")
    if reasoning_tokens is not None:
        parts.append(f"reasoning_tokens={reasoning_tokens}")
    if output_types:
        parts.append(f"output_item_types={output_types!r}")
    if error:
        parts.append(f"provider_error={error!s}")
    if reason == "max_output_tokens":
        parts.append("Increase max_output_tokens or lower the model's reasoning effort")
    return "; ".join(parts)


class OACypherAgent:
    def __init__(
        self,
        llm_service: str = "open_router",
        *,
        max_output_tokens: int = 4096,
        model: str | None = None,
        client: Any | None = None,
        reasoning_effort: str | None = None,
    ):
        load_dotenv()
        if llm_service not in ("open_ai", "open_router"):
            raise ValueError("llm_service must be 'open_ai' or 'open_router'")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")

        self.llm_service = llm_service
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        prefix = "OPENAI" if llm_service == "open_ai" else "OPENROUTER"
        self.model = (model or os.getenv(f"{prefix}_MODEL") or "").strip()
        if not self.model:
            raise ValueError(f"Configure {prefix}_MODEL in .env or pass model=...")
        
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI
            
            api_key = os.getenv(f"{prefix}_API_KEY")
            if not api_key:
                raise ValueError(f"Configure {prefix}_API_KEY in .env")
            options = {"api_key": api_key}
            if llm_service == "open_router":
                options["base_url"] = "https://openrouter.ai/api/v1"
            self.client = OpenAI(**options)
        
    def _generate_one(self, base_prompt: str, datapoint: dict) -> str:
        request = {
            "model": self.model,
            "instructions": base_prompt,
            "input": f"Graph Schema:\n{datapoint['schema']}\n\nQuestion:\n{datapoint['question']}",
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        
        response = self.client.responses.create(**request)
        if _attribute(response, "status") in ("failed", "incomplete", "cancelled", "in_progress", "queued"):
            raise EmptyModelResponseError(_response_diagnostic(response, self.model))
        text = _visible_text(response)
        if not text:
            raise EmptyModelResponseError(_response_diagnostic(response, self.model))
        return text
    
    def generate(self, base_prompt: str, batch: list[dict], batch_limit: int = 10) -> list[str]:
        """Original fail-fast API for callers that expect exactly one query per row."""
        if len(batch)>batch_limit:
            raise ValueError("batch_size exceeds api price safety limit")
        return [self._generate_one(base_prompt, row) for row in batch]
        
    def generate_with_errors(
        self, base_prompt: str, batch: list[dict], batch_limit: int = 10
    ) -> tuple[list[str | None], list[dict[str, Any]]]:
        """Continue after empty model responses, preserving each row's index.
        
        Actual API/configuration errors still propagate; retrying those ten
        times would not be useful and could increase API costs.
        """
        if len(batch) > batch_limit:
            raise ValueError("batch_size exceeds api price safety limit")
        queries: list[str | None] = []
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(batch):
            try:
                queries.append(self._generate_one(base_prompt, row))
            except EmptyModelResponseError as exc:
                queries.append(None)
                errors.append({
                    "index": index,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
        return queries, errors
                
            
# Retain the user's original class name alongside the project's existing name.
# Both refer to the same hardened implementation and Responses API handling.
CypherAgent = OACypherAgent
