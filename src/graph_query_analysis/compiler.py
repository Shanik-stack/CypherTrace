"""Composing the transforms in AST clause order"""


"""Compose Cypher clause transformations into successive symbolic states.

Each state describes the query after one more clause. Nothing is executed
against a database, and no correctness claims are proved here.
"""

from graph_query_analysis.model import AnalysisRequest, SymbolicState, UnitBag
from graph_query_analysis.transforms import TransformError, UnsupportedCypherError, transform_clause
from graph_query_analysis.graph import GraphSnapshot


def compile_request(
    request: AnalysisRequest,
) -> tuple[tuple[SymbolicState, ...] | None, tuple[dict, ...]] |None:

    clauses = request.clauses

    state = SymbolicState(
        schema=request.schema,
        scope=(),
        bag=UnitBag(),
        graph=GraphSnapshot.empty(),
    )

    success_states = [state]
    failed_states = []
    index = None
    kind = None

    try:
        if not clauses:
            raise TransformError("Query contains no clauses")

        if clauses[-1]["type"] != "Return":
            raise UnsupportedCypherError(
                "Only queries ending in RETURN are supported"
            )

        for index, clause in enumerate(clauses, start=1):
            kind = clause["type"]

            if kind == "Return" and index != len(clauses):
                raise UnsupportedCypherError(
                    "RETURN must be the final clause"
                )

            state = transform_clause(state, clause)
            success_states.append(state)

    except (TransformError, UnsupportedCypherError) as exc:
        failed_states.append({
            "request_id": request.request_id,
            "query": request.query_text,
            "clause_index": index,
            "clause_type": kind,
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        return None

    return tuple(success_states), tuple(failed_states)

def compile_requests(requests: list[AnalysisRequest] | tuple[AnalysisRequest]):
    all_states, all_failed = [], []
    for query_idx in range(len(requests)):
            analysis_request = analysis_requests[query_idx]
            states, failed_states = compile_request(analysis_request)
            all_states.append(states)
            all_failed.append(all_failed)
    return all_states, all_failed
            



if __name__ == "__main__":
    from pathlib import Path
    import json
    path = Path(r"graph_analysis_requests\cypherast_lean_request.json")
    with open(path, 'r') as f:
        json_request = json.load(f)
    json_request_0 = json_request[0]
    
    from graph_query_analysis.input import load_requests
    analysis_requests = load_requests(path)
    for query_idx in range(len(analysis_requests)):
        print(f"\n\n ---------------- QUERY: {query_idx}------------\n\n")
        analysis_request = analysis_requests[query_idx]
        print(analysis_request.query_text)
        print("\n\n ---------------- STATES ------------\n\n")
        states, failed_states = compile_request(analysis_request)
        print("\n\n ------ 10 FAILED STATES ----\n\n")
        print(failed_states[:10])
        print("------------------\n\n")
        print(analysis_request)

        def bag_chain(bag):
            names = []

            while True:
                names.append(type(bag).__name__)
                if not hasattr(bag, "source"):
                    break
                bag = bag.source

            return " → ".join(reversed(names))


        for i, state in enumerate(states):
            clause = "INITIAL" if i == 0 else analysis_request.clauses[i - 1]["type"]

            print(f"\nState {i} ({clause})")
            print("Scope:", [b.variable for b in state.scope])
            print("Bag:", bag_chain(state.bag))

            graph = state.graph.graph

            print("Nodes:")
            for symbol, data in graph.nodes(data=True):
                print(" ", symbol.name, data.get("labels"))

            print("Relationships:")
            for left, right, key, data in graph.edges(keys=True, data=True):
                print(" ", left.name, "->", right.name, data.get("types"))
