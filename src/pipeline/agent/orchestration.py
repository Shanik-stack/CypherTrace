"""Generate → parse/compile/report → optionally critique → revise Cypher.

This module orchestrates existing components; it does not implement a second
Cypher parser or interpreter. No database queries, formal proofs, or CLI calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from pipeline.agent.prompts import base_prompt1, revision_prompt
from pipeline.agent.cypher_agent import EmptyModelResponseError
from pipeline.agent.critic_agent import Critique, CriticResponseError

Status = Literal["compiled", "compilation_failed", "parse_failed"]


@dataclass(frozen=True)
class CandidateAnalysis:
    status: Status
    report_text: str
    errors: tuple[str, ...] = ()
    

@dataclass(frozen=True)
class Attempt:
    number: int
    query: str
    analysis: CandidateAnalysis
    critique: Critique | None = None
    # Preserve what was sent to the generator and the exact query it returned.
    revision_source: str | None = None
    revision_feedback: str | None = None
    revision_response: str | None = None
    revision_error: str | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    """Full history, including rejected queries and reasons for stopping."""
    final_query: str | None
    status: Status | Literal["generation_failed"]
    stop_reason: str
    attempts: tuple[Attempt, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_query": self.final_query,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "attempts": [
                {
                    "number": attempt.number,
                    "query": attempt.query,
                    "status": attempt.analysis.status,
                    "report": attempt.analysis.report_text,
                    "errors": list(attempt.analysis.errors),
                    "critique": None if attempt.critique is None else {
                        "needs_revision": attempt.critique.needs_revision,
                        "feedback": attempt.critique.feedback,
                    },
                    "revision_source": attempt.revision_source,
                    "revision_feedback": attempt.revision_feedback,
                    "revision_response": attempt.revision_response,
                    "revision_error": attempt.revision_error,
                }
                for attempt in self.attempts
            ],
        }


def normalize_query(query: str) -> str:
    """Remove Markdown fences and one optional statement terminator.

    Only the final semicolon is removed. Internal semicolons are left for the
    parser to accept or reject (we do not silently support multiple queries).
    """
    if not isinstance(query, str):
        raise TypeError("Generated Cypher must be a string")
    text = query.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in ("```cypher", "```") and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    text = text.removesuffix(";").rstrip()
    if not text:
        raise ValueError("Generated Cypher is empty")
    return text


def _schema_object(schema: dict | str) -> dict:
    value = json.loads(schema) if isinstance(schema, str) else schema
    if not isinstance(value, dict):
        raise TypeError("schema must be a dict or JSON object string")
    return value


def analyze_candidate(query: str, schema: dict, *, show_formulas: bool = False) -> CandidateAnalysis:
    """Use the actual project parser, compiler and symbolic reporter in memory.

    Compilation status means only that this engine understood the query. It is
    not a guarantee that the query answers the question or matches any records.
    """
    from pipeline.lean_request.cypher_parser import cypherast_parser
    from graph_query_analysis.input import parse_requests
    from graph_query_analysis.report import generate_report

    serialized, parse_errors = cypherast_parser().build_lean_request(
        [query], [schema], write_file=False
    )
    if parse_errors:
        messages = tuple(str(item["error"]) for item in parse_errors)
        return CandidateAnalysis("parse_failed", "Cypher parsing failed:\n" + "\n".join(messages), messages)
    if len(serialized) != 1:
        raise ValueError("Parser must produce exactly one request for one successful query")

    request = parse_requests(serialized)[0]
    report = generate_report(request)
    if report.status == "compilation_failed":
        message = report.compilation_error or "Query could not be compiled"
        return CandidateAnalysis("compilation_failed", report.render(show_formulas=show_formulas), (message,))
    return CandidateAnalysis("compiled", report.render(show_formulas=show_formulas))


class QueryOrchestration:
    """Generate, analyze, critique, and revise Cypher queries."""

    def __init__(
        self,
        with_critic: bool = False,
        *,
        max_revisions: int = 2,
        generator: Any | None = None,
        critic: Any | None = None,
        analyzer: Callable[[str, dict], CandidateAnalysis] = analyze_candidate,
        max_report_chars: int = 8000,
        improve_without_critic: bool = False,
        generation_prompt: str = base_prompt1,
    ):
        if type(max_revisions) is not int or max_revisions < 0:
            raise ValueError("max_revisions must be a nonnegative integer")

        if type(max_report_chars) is not int or max_report_chars <= 0:
            raise ValueError("max_report_chars must be positive")

        self.with_critic = with_critic
        self.max_revisions = max_revisions
        self.generator = generator
        self.critic = critic
        self.analyzer = analyzer
        self.max_report_chars = max_report_chars
        self.improve_without_critic = improve_without_critic
        self.generation_prompt = generation_prompt

    def _generator(self):
        if self.generator is None:
            from pipeline.agent.cypher_agent import CypherAgent
            self.generator = CypherAgent()

        return self.generator

    def _critic(self):
        if self.critic is None:
            from pipeline.agent.critic_agent import CriticAgent
            self.critic = CriticAgent()

        return self.critic

    def _generate(
        self,
        question: str,
        schema: dict,
        instructions: str,
    ) -> str:

        generated = self._generator().generate(
            base_prompt=instructions,
            batch=[{
                "question": question,
                "schema": schema,
            }],
        )

        if len(generated) != 1:
            raise ValueError(
                "Generator must return exactly one query"
            )

        if generated[0] is None:
            raise EmptyModelResponseError(
                "Generator did not produce visible Cypher"
            )

        try:
            return normalize_query(generated[0])
        except ValueError as exc:
            raise EmptyModelResponseError(
                "Generator produced empty Cypher"
            ) from exc

    def run(
        self,
        question: str,
        schema: dict | str,
        *,
        initial_query: str | None = None,
    ) -> OrchestrationResult:
        """Process one question through a bounded revision loop."""

        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be nonempty")

        schema_obj = _schema_object(schema)

        attempts: list[Attempt] = []
        seen: set[str] = set()

        def finish(
            reason: str,
            error: str | None = None,
        ) -> OrchestrationResult:

            last = attempts[-1] if attempts else None

            return OrchestrationResult(
                final_query=last.query if last else None,
                status=(
                    last.analysis.status
                    if last
                    else "generation_failed"
                ),
                stop_reason=reason,
                attempts=tuple(attempts),
                error=error,
            )

        # Generate the first query, unless one was supplied.
        try:
            query = (
                normalize_query(initial_query)
                if initial_query is not None
                else self._generate(
                    question,
                    schema_obj,
                    self.generation_prompt,
                )
            )
        except (EmptyModelResponseError, ValueError) as exc:
            return finish("generation_error", str(exc))

        # Initial query + up to max_revisions revisions.
        for number in range(1, self.max_revisions + 2):

            if query in seen:
                return finish("repeated_query")

            seen.add(query)

            # Parse, compile, and generate symbolic report.
            analysis = self.analyzer(query, schema_obj)

            critique: Critique | None = None

            # Ask the critic only if compilation succeeded.
            if analysis.status == "compiled" and self.with_critic:

                summary = analysis.report_text

                if len(summary) > self.max_report_chars:
                    summary = (
                        summary[:self.max_report_chars]
                        + "\n[Report truncated for critic]"
                    )

                try:
                    critique = self._critic().review(
                        question=question,
                        schema=schema_obj,
                        query=query,
                        report=summary,
                    )

                except (
                    CriticResponseError,
                    EmptyModelResponseError,
                ) as exc:
                    attempts.append(Attempt(number, query, analysis))
                    return finish("critic_error", str(exc))

            # Save this attempt and its analysis.
            attempts.append(
                Attempt(number, query, analysis, critique)
            )

            # Decide what feedback should guide the next query.
            if analysis.status == "compiled":

                if self.with_critic:

                    if critique is None:
                        return finish(
                            "critic_error",
                            "Critic returned no feedback",
                        )

                    if not critique.needs_revision:
                        return finish("critic_accepted")

                    feedback = critique.feedback
                    source = "llm_critic"

                elif self.improve_without_critic:

                    feedback = analysis.report_text
                    source = "symbolic_report"

                else:
                    return finish("compiled_without_critic")

            else:
                # Parsing or compilation failed.
                feedback = analysis.report_text

                if analysis.errors:
                    feedback += (
                        "\n\nDiagnostics:\n"
                        + "\n".join(analysis.errors)
                    )

                source = "parse_or_compilation_report"

            # Check whether further revisions are allowed.
            if number > self.max_revisions:
                return finish("revision_limit")

            # Limit report/feedback length sent to the LLM.
            if len(feedback) > self.max_report_chars:
                feedback = (
                    feedback[:self.max_report_chars]
                    + "\n[Report truncated for revision]"
                )

            # Store the feedback that will be used.
            attempts[-1] = replace(
                attempts[-1],
                revision_source=source,
                revision_feedback=feedback,
            )

            # Build the next generation prompt.
            instructions = (
                self.generation_prompt
                + "\n\n"
                + revision_prompt.format(
                    previous_query=query,
                    feedback=feedback,
                )
            )

            # Generate the revised query.
            try:
                query = self._generate(
                    question,
                    schema_obj,
                    instructions,
                )

            except EmptyModelResponseError as exc:

                attempts[-1] = replace(
                    attempts[-1],
                    revision_error=str(exc),
                )

                return finish("generation_error", str(exc))

            # Record the generated revision in this attempt.
            attempts[-1] = replace(
                attempts[-1],
                revision_response=query,
            )

        raise AssertionError(
            "Bounded revision loop unexpectedly fell through"
        )

    def run_batch(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        query_source: Literal["agent", "reference"] = "agent",
        batch_limit: int = 10,
    ) -> tuple[OrchestrationResult, ...]:
        """Process multiple questions independently."""

        if query_source not in ("agent", "reference"):
            raise ValueError(
                "query_source must be 'agent' or 'reference'"
            )

        if not 0 < len(records) <= batch_limit:
            raise ValueError(
                f"Number of records must be in [1, {batch_limit}]"
            )

        return tuple(
            self.run(
                question=str(record["question"]),
                schema=record["schema"],
                initial_query=(
                    record["cypher"]
                    if query_source == "reference"
                    else None
                ),
            )
            for record in records
        )


def save_results(
    results: OrchestrationResult | Sequence[OrchestrationResult], path: str | Path
) -> Path:
    """Opt-in JSON history export; does not read a stale report from disk."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = results.to_dict() if isinstance(results, OrchestrationResult) else [r.to_dict() for r in results]
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


# Existing user-facing spelling remains available without duplicating logic.
Query_Orchestration = QueryOrchestration
