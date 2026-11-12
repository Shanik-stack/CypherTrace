"""Compile a single Cypher query into a complete sequence of symbolic states.

Compilation is sequential and fail-fast: downstream transformations must never
be applied to a stale state. Use diagnostics.diagnose_request for independent,
non-sequential checks across all clauses; compile_requests handles batches.
"""

from pathlib import Path
from typing import Sequence

from graph_query_analysis.graph import GraphSnapshot
from graph_query_analysis.model import AnalysisRequest, SymbolicState, UnitBag
from graph_query_analysis.transforms import (
    TransformError, UnsupportedCypherError, transform_clause,
)


def compile_request_progress(request: AnalysisRequest) -> tuple[tuple[SymbolicState, ...], Exception | None]:
    """Compile in order; retain valid states and the first failed transition.

    This is used by reporting so a query failing at clause N still has an
    explanation of clauses 1..N-1. It never applies a later clause to an
    invalid state.
    """


    state = SymbolicState(
        schema=request.schema,
        scope=(),
        bag=UnitBag(),
        graph=GraphSnapshot.empty(),
    )
    states = [state]
    clauses = request.clauses
    if not clauses:
        return tuple(states), TransformError("Query contains no clauses")

    for index, clause in enumerate(clauses, start=1):
        kind = clause["type"]
        try:
            if kind == "Return" and index != len(clauses):
                raise UnsupportedCypherError("RETURN must be the final clause")
            state = transform_clause(state, clause)
        except (TransformError, UnsupportedCypherError) as exc:
            return tuple(states), type(exc)(f"Clause {index} ({kind}): {exc}")
        states.append(state)

    if clauses[-1]["type"] != "Return":
        return tuple(states), UnsupportedCypherError("Only queries ending in RETURN are supported")
    return tuple(states), None


def compile_request(request: AnalysisRequest) -> tuple[SymbolicState, ...]:
    """Return all states, or raise the first compilation error."""
    states, error = compile_request_progress(request)
    if error is not None:
        raise error
    return states


def compile_requests(
    requests: Sequence[AnalysisRequest],
) -> tuple[
    tuple[tuple[SymbolicState, ...] | None, ...],
    tuple[tuple[dict, ...], ...],
]:
    """Compile each query independently, preserving input order.
            
    The second result contains *all independently detectable AST issues* for
    each failed query plus the first sequential compiler error. Errors cannot
    be exhaustively inferred when they depend on unavailable previous states.
    """
    from graph_query_analysis.diagnostics import diagnose_request

    states_by_request = []
    errors_by_request = []
    for request in requests:
        try:
            states = compile_request(request)
        except (TransformError, UnsupportedCypherError) as exc:
            states_by_request.append(None)
            errors_by_request.append(diagnose_request(request, compilation_error=exc))
        else:
            states_by_request.append(states)
            errors_by_request.append(())
    return tuple(states_by_request), tuple(errors_by_request)


def compile_file(requests_path: str | Path):
    """Function-based batch entrypoint (no command-line arguments needed)."""
    from graph_query_analysis.input import load_requests
    return compile_requests(load_requests(requests_path))


if __name__ == "__main__":
    requests_path = Path("lean_requests_json/cypherast_lean_request.json")
    compiled, diagnostics = compile_file(requests_path)
    print(f"Total requests: {len(compiled)}")
    print(f"Compiled successfully: {sum(s is not None for s in compiled)}")
    print(f"Failed compilation: {sum(s is None for s in compiled)}")
    for result in diagnostics:
        for issue in result:
            print(f"{issue['request_id']} | {issue['code']}: {issue['error']}")
