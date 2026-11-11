"""Run the query-generation -> CypherAST -> symbolic-report pipeline.

Call ``main(...)`` with ordinary Python arguments; there is no CLI parser.
Each invocation selects a small set of dataset rows. Query generation with an
LLM is opt-in via ``query_source='agent'``; ``'reference'`` uses dataset Cypher
queries and requires no external API call. Neither path executes Cypher in Neo4j.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from graph_query_analysis.input import parse_requests
from graph_query_analysis.report import QueryReport, generate_report
from pipeline.agent.orchestration import (
    OrchestrationResult, QueryOrchestration, analyze_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QuerySource = Literal["agent", "reference"]
DatasetName = Literal["mtq", "cypherbench"]
    

@dataclass(frozen=True)
class PipelineResult:
    """One batch of reports, aligned with parsed requests (not failed parses)."""
    
    reports: tuple[QueryReport, ...]
    parsing_errors: tuple[dict[str, Any], ...]
    requests_path: Path
    report_path: Path
    errors_path: Path
    attempted_queries: int
    generation_errors: tuple[dict[str, Any], ...] = ()
    
    @property
    def compiled_count(self) -> int:
        return sum(report.status == "compiled" for report in self.reports)
    
    @property
    def compilation_failed_count(self) -> int:
        return sum(report.status == "compilation_failed" for report in self.reports)


@dataclass(frozen=True)
class OrchestrationPipelineResult:
    """Full revision histories and one symbolic report file per trial."""

    results: tuple[OrchestrationResult, ...]
    history_paths: tuple[Path, ...]
    history_json_paths: tuple[Path, ...]
    trial_report_paths: tuple[tuple[Path, ...], ...]
    requests_path: Path      # JSON summaries, not serialized CypherAST requests
    report_path: Path        # Index of the per-question history files
    errors_path: Path
    attempted_queries: int
    parsing_errors: tuple[dict[str, Any], ...] = ()
    generation_errors: tuple[dict[str, Any], ...] = ()

    @property
    def compiled_count(self) -> int:
        return sum(r.status == "compiled" for r in self.results)

    @property
    def compilation_failed_count(self) -> int:
        return sum(r.status == "compilation_failed" for r in self.results)

    @property
    def parsed_count(self) -> int:
        return sum(r.status in ("compiled", "compilation_failed") for r in self.results)


def _history_text(
    question: str,
    result: OrchestrationResult,
    report_paths: Sequence[Path],
) -> str:
    """Write the chronological question → candidates → LLM feedback trace."""
    lines = ["Question:", question, ""]
    for attempt, path in zip(result.attempts, report_paths):
        lines.extend([
            f"Query {attempt.number}:",
            attempt.query,
            "",
            f"Trial {attempt.number} status: {attempt.analysis.status}",
            f"Trial {attempt.number} symbolic report: {path.name}",
            "",
        ])
        if attempt.critique is not None:
            lines.extend([
                "LLM critic response:",
                f"needs_revision: {attempt.critique.needs_revision}",
                f"feedback: {attempt.critique.feedback}",
                "",
            ])
        if attempt.revision_source == "llm_critic":
            lines.extend([
                "Critic feedback supplied to query generator:",
                attempt.revision_feedback or "",
                "",
            ])
        elif attempt.revision_source is not None:
            lines.extend([
                "Report-based improvement feedback supplied to LLM:",
                attempt.revision_feedback or "",
                "",
            ])
        if attempt.revision_response is not None:
            lines.extend([
                f"LLM improvement response (Query {attempt.number + 1}):",
                attempt.revision_response,
                "",
            ])
        if attempt.revision_error is not None:
            lines.extend(["LLM improvement error:", attempt.revision_error, ""])

    lines.extend([
        "Final status: " + result.status,
        "Stop reason: " + result.stop_reason,
    ])
    if result.error:
        lines.append("Error: " + result.error)
    return "\n".join(lines).rstrip() + "\n"


def _run_orchestration(
    records: Sequence[Mapping[str, Any]],
    schemas: Sequence[dict[str, Any]],
    *,
    query_source: QuerySource,
    agent: Any | None,
    critic: Any | None,
    orchestrator: QueryOrchestration | None,
    base_prompt: str | None,
    output_dir: str | Path | None,
    show_formulas: bool,
    file_name: str,
    with_critic: bool,
    max_revisions: int | None,
    improve_without_critic: bool,
    max_report_chars: int,
    record_indices: Sequence[int] | None,
) -> OrchestrationPipelineResult:
    if query_source == "agent" and len(records) > 10:
        raise ValueError("Agent orchestration is limited to 10 records per run")
    if record_indices is not None and len(record_indices) != len(records):
        raise ValueError("record_indices must match the number of records")

    if orchestrator is None:
        from pipeline.agent.prompts import base_prompt1

        # Reference mode remains API-free by default. Revisions or an LLM critic
        # for reference queries must be explicitly requested by the caller.
        revisions = (2 if query_source == "agent" else 0) if max_revisions is None else max_revisions
        orchestrator = QueryOrchestration(
            with_critic=with_critic,
            max_revisions=revisions,
            generator=agent,
            critic=critic,
            analyzer=lambda q, sch: analyze_candidate(
                q, sch, show_formulas=show_formulas,
            ),
            improve_without_critic=improve_without_critic and revisions > 0,
            max_report_chars=max_report_chars,
            generation_prompt=base_prompt if base_prompt is not None else base_prompt1,
        )

    destination = Path(output_dir) if output_dir is not None else PROJECT_ROOT / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    requests_path = destination / f"{file_name}_orchestration.json"
    report_path = destination / f"{file_name}_report.txt"
    errors_path = destination / f"{file_name}_errors.json"

    results: list[OrchestrationResult] = []
    histories: list[Path] = []
    history_jsons: list[Path] = []
    trial_reports: list[tuple[Path, ...]] = []
    parsing_errors: list[dict[str, Any]] = []
    compilation_errors: list[dict[str, Any]] = []
    generation_errors: list[dict[str, Any]] = []
    critic_errors: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for position, (record, schema) in enumerate(zip(records, schemas)):
        source_index = position if record_indices is None else record_indices[position]
        stem = f"{file_name}_query_{source_index}"
        if stem in used_names:
            stem += f"_input_{position}"
        used_names.add(stem)
        question = str(record["question"])
        initial_query = record["cypher"] if query_source == "reference" else None
        result = orchestrator.run(question, schema, initial_query=initial_query)
        results.append(result)

        paths: list[Path] = []
        for attempt in result.attempts:
            # Each regeneration gets a separate file, never a stale shared report.
            trial_path = destination / f"{stem}_trial{attempt.number}_report.txt"
            trial_path.write_text(attempt.analysis.report_text, encoding="utf-8")
            paths.append(trial_path)
            problem = {
                "input_index": position,
                "source_index": source_index,
                "trial": attempt.number,
                "query": attempt.query,
                "error": list(attempt.analysis.errors),
            }
            if attempt.analysis.status == "parse_failed":
                parsing_errors.append(problem)
            elif attempt.analysis.status == "compilation_failed":
                compilation_errors.append(problem)

        if result.status == "generation_failed" or result.stop_reason == "generation_error":
            generation_errors.append({
                "input_index": position,
                "source_index": source_index,
                "error": result.error or "No model response",
            })

        if result.stop_reason == "critic_error":
            critic_errors.append({
                "input_index": position,
                "source_index": source_index,
                "error": result.error or "Critic did not produce valid feedback",
            })

        history_path = destination / f"{stem}_history.txt"
        history_path.write_text(_history_text(question, result, paths), encoding="utf-8")
        history_json_path = destination / f"{stem}_history.json"
        history_json_path.write_text(json.dumps({
            "question": question,
            "input_index": position,
            "source_index": source_index,
            "trial_report_files": [str(path) for path in paths],
            **result.to_dict(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        histories.append(history_path)
        history_jsons.append(history_json_path)
        trial_reports.append(tuple(paths))
        summaries.append({
            "question": question,
            "input_index": position,
            "source_index": source_index,
            "final_query": result.final_query,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "attempts": len(result.attempts),
            "history_file": str(history_path),
            "trial_reports": [str(path) for path in paths],
        })

    requests_path.write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report_path.write_text("\n".join(
        f"Input {s['input_index']} / dataset {s['source_index']}: "
        f"{s['status']} ({s['attempts']} trials)\n"
        f"History: {s['history_file']}"
        for s in summaries
    ) + "\n", encoding="utf-8")
    errors_path.write_text(json.dumps({
        "generation_errors": generation_errors,
        "critic_errors": critic_errors,
        "parsing_errors": parsing_errors,
        "compilation_failures": compilation_errors,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return OrchestrationPipelineResult(
        results=tuple(results),
        history_paths=tuple(histories),
        history_json_paths=tuple(history_jsons),
        trial_report_paths=tuple(trial_reports),
        requests_path=requests_path,
        report_path=report_path,
        errors_path=errors_path,
        attempted_queries=len(records),
        parsing_errors=tuple(parsing_errors),
        generation_errors=tuple(generation_errors),
    )


def _dataset_records(
    dataset_name: DatasetName,
    split: str,
    indices: Sequence[int],
    validation_type: str,
) -> list[dict[str, Any]]:
    """Select rows; MTQ schemas were normalized at dataset load time."""
    if dataset_name == "mtq":
        from pipeline.data.load_data import MTQ_dataset

        dataset = MTQ_dataset(validation_type=validation_type)
        if split not in dataset.dataset:
            raise ValueError(f"MTQ split {split!r} is unavailable")
        rows = dataset.dataset[split]
    elif dataset_name == "cypherbench":
        from pipeline.data.load_data import CypherBench_dataset

        if split != "test":
            raise ValueError("CypherBench_dataset only supplies the test split")
        rows = CypherBench_dataset()
    else:
        raise ValueError(f"Unknown dataset: {dataset_name!r}")

    selected = []
    for index in indices:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(rows):
            raise IndexError(f"Dataset index out of range: {index!r}")
        selected.append(dict(rows[index]))
    return selected


def run_pipeline(
    records: Sequence[Mapping[str, Any]],
    *,
    query_source: QuerySource = "reference",
    agent: Any | None = None,
    base_prompt: str | None = None,
    output_dir: str | Path | None = None,
    show_formulas: bool = False,
    file_name: str = "pipeline_requests",
    orchestrate: bool = False,
    with_critic: bool = False,
    max_revisions: int | None = None,
    improve_without_critic: bool = True,
    max_report_chars: int = 8000,
    critic: Any | None = None,
    orchestrator: QueryOrchestration | None = None,
    record_indices: Sequence[int] | None = None,
) -> PipelineResult | OrchestrationPipelineResult:
    """Process supplied normalized records through parsing and report generation.

    Each record needs ``question``, ``schema`` and, for reference mode, ``cypher``.
    ``schema`` can be a normalized dict or a JSON string. Parse failures are
    saved separately; valid parsed requests are compiled and reported one by
    one, including structured compilation-failure reports.
    """
    records = list(records)
    if not records:
        raise ValueError("At least one record is required")
    if query_source not in ("agent", "reference"):
        raise ValueError("query_source must be 'agent' or 'reference'")
    if not file_name or Path(file_name).name != file_name or file_name in (".", ".."):
        raise ValueError("file_name must be a simple filename stem")

    schemas: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        raw = row["schema"]
        schema = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(schema, dict):
            raise TypeError(f"records[{index}].schema must be an object")
        schemas.append(schema)

    if orchestrate:
        return _run_orchestration(
            records, schemas,
            query_source=query_source,
            agent=agent,
            critic=critic,
            orchestrator=orchestrator,
            base_prompt=base_prompt,
            output_dir=output_dir,
            show_formulas=show_formulas,
            file_name=file_name,
            with_critic=with_critic,
            max_revisions=max_revisions,
            improve_without_critic=improve_without_critic,
            max_report_chars=max_report_chars,
            record_indices=record_indices,
        )

    generation_errors: tuple[dict[str, Any], ...] = ()
    source_indices = list(range(len(records)))

    if query_source == "agent":
        if len(records) > 10:
            raise ValueError("Agent generation is limited to 10 selected records per run")
        if agent is None:
            from pipeline.agent import OACypherAgent
            agent = OACypherAgent()
        if base_prompt is None:
            from pipeline.agent import base_prompt1
            base_prompt = base_prompt1
        # Preserve the existing generate() contract for custom agents, while
        # allowing the bundled agent to report per-row empty responses.
        batch = [
            {**dict(row), "schema": schema}
            for row, schema in zip(records, schemas)
        ]
        if callable(getattr(agent, "generate_with_errors", None)):
            generated, raw_generation_errors = agent.generate_with_errors(
                base_prompt=base_prompt, batch=batch
            )
            generation_errors = tuple(
                {**error, "input_index": error["index"]}
                for error in raw_generation_errors
            )
        else:
            generated = agent.generate(base_prompt=base_prompt, batch=batch)

        if len(generated) != len(records):
            raise ValueError("Query generator returned a different number of queries than records")
        source_indices = [i for i, query in enumerate(generated) if query is not None]
        queries = [generated[i] for i in source_indices]
    else:
        queries = [row["cypher"] for row in records]

    if not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("Every generated Cypher query must be a nonempty string")

    if query_source == "agent":
        # Strip only a surrounding Markdown fence, if the model adds one.
        cleaned = []
        for query in queries:
            lines = query.strip().splitlines()
            if len(lines) >= 3 and lines[0].strip().lower() in ("```cypher", "```") and lines[-1].strip() == "```":
                query = "\n".join(lines[1:-1])
            cleaned.append(query.strip())
        queries = cleaned

    from pipeline.lean_request.cypher_parser import cypherast_parser
    parser = cypherast_parser()
    requests_json, parsing_errors = parser.build_lean_request(
        queries, [schemas[i] for i in source_indices], write_file=False
    )

    # The parser skips failed queries, so the requests remain a shorter list.
    # Preserve the original input index in diagnostics for unambiguous tracking.
    parsing_errors = tuple(
        {**error, "input_index": source_indices[error["index"]]}
        for error in parsing_errors
    )
    # The parser numbers only submitted queries. Restore indices in the
    # original batch when some LLM generations were skipped.
    for item in requests_json:
        request_id = item.get("request_id", "")
        if request_id.startswith("query_") and request_id[6:].isdigit():
            local_index = int(request_id[6:])
            item["request_id"] = f"query_{source_indices[local_index]}"

    analysis_requests = parse_requests(requests_json) if requests_json else ()
    reports = tuple(generate_report(request) for request in analysis_requests)

    destination = Path(output_dir) if output_dir is not None else PROJECT_ROOT / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    requests_path = destination / f"{file_name}.json"
    report_path = destination / f"{file_name}_report.txt"
    errors_path = destination / f"{file_name}_errors.json"

    requests_path.write_text(json.dumps(requests_json, indent=2), encoding="utf-8")
    report_path.write_text(
        "\n\n".join(report.render(show_formulas=show_formulas) for report in reports),
            encoding="utf-8",
        )
    error_payload = {
        "parsing_errors": parsing_errors,
        "compilation_failures": [
            {"request_id": request.request_id, "query": request.query_text,
             "error": report.compilation_error}
            for request, report in zip(analysis_requests, reports)
            if report.status == "compilation_failed"
        ],
    }
    if generation_errors:
        error_payload["generation_errors"] = generation_errors
    errors_path.write_text(json.dumps(error_payload, indent=2), encoding="utf-8")

    return PipelineResult(
        reports=reports,
        parsing_errors=parsing_errors,
        requests_path=requests_path,
        report_path=report_path,
        errors_path=errors_path,
        attempted_queries=len(records),
        generation_errors=generation_errors,
    )


def main(
    *,
    dataset_name: DatasetName = "mtq",
    split: str = "train",
    indices: Sequence[int] = (0,),
    query_source: QuerySource = "agent",
    validation_type: str = "manual",
    output_dir: str | Path | None = None,
    show_formulas: bool = False,
    agent: Any | None = None,
    base_prompt: str | None = None,
    orchestrate: bool = True,
    with_critic: bool = False,
    max_revisions: int | None = None,
    improve_without_critic: bool = True,
    max_report_chars: int = 8000,
    critic: Any | None = None,
    orchestrator: QueryOrchestration | None = None,
    file_name: str = "pipeline_requests",
) -> PipelineResult | OrchestrationPipelineResult:
    """Load dataset rows and optionally run the full revision orchestration."""
    records = _dataset_records(dataset_name, split, indices, validation_type)
    return run_pipeline(
        records,
        query_source=query_source,
        agent=agent,
        base_prompt=base_prompt,
        output_dir=output_dir,
        show_formulas=show_formulas,
        file_name=file_name,
        orchestrate=orchestrate,
        with_critic=with_critic,
        max_revisions=max_revisions,
        improve_without_critic=improve_without_critic,
        max_report_chars=max_report_chars,
        critic=critic,
        orchestrator=orchestrator,
        record_indices=indices,
    )


if __name__ == "__main__":
    # Use indices=list(range(10)) when ready for up to 30 LLM generations
    # (10 questions * 3 candidate trials), plus optional critic calls.
    result = main(
        dataset_name="mtq",
        split="train",
        indices=(0,),
        query_source="agent",
        orchestrate=True,
        with_critic=False,
        max_revisions=2,
    )
    print(f"Questions: {result.attempted_queries}")
    print(f"Compiled final queries: {result.compiled_count}")
    print(f"Compilation failures: {result.compilation_failed_count}")
    print(f"Parse failures: {len(result.parsing_errors)}")
    print(f"Generation failures: {len(result.generation_errors)}")
    print(f"Summary: {result.report_path}")
    print(f"Errors: {result.errors_path}")
    if isinstance(result, OrchestrationPipelineResult):
        for path in result.history_paths:
            print(f"History: {path}")
