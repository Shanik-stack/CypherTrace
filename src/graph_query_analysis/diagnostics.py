"""Independent diagnostics for the currently supported CypherAST subset.

Walk *every* serialized clause to find statically detectable unsupported
constructs, without fabricating intermediate symbolic states. A separate
sequential compilation attempt contributes its first scope/type/transform error.
This is not an exhaustive Cypher validator and never queries Neo4j.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from graph_query_analysis.model import AnalysisRequest
from graph_query_analysis.transforms import TransformError, UnsupportedCypherError

# These AST nodes cannot currently be interpreted by transforms._predicate.
# Do not label all unfamiliar AST nodes as invalid Cypher: the parser and
# transformer have different responsibilities.
_UNSUPPORTED_EXPRESSIONS = frozenset({
    "In", "Regex", "RegexMatch",
    "Xor", "Case", "ListComprehension", "PatternComprehension",
    "Reduce", "AllPredicate", "AnyPredicate", "NonePredicate",
    "SinglePredicate",
})

_SUPPORTED_CLAUSES = frozenset({"Match", "With", "Return"})

_ALLOWED_ARGS = {
    "Match": {"pattern", "optional", "where", "hints", "search"},
    "With": {"expressions", "distinct", "where", "order", "skip", "limit", "group_by"},
    "Return": {"expressions", "distinct", "where", "order", "skip", "limit", "group_by"},
    "Pattern": {"paths"},
    "PathPattern": {"variable", "elements"},
    "NodePattern": {"variable", "labels", "properties", "where"},
    "RelationshipPattern": {
        "variable", "types", "properties", "direction", "min_hops", "max_hops",
        "variable_length", "where", "memgraph_quantifier", "memgraph_weight_expr",
        "memgraph_total_weight",
    },
    "Order": {"expressions"},
    "OrderBy": {"expressions"},
    "Ordered": {"this", "desc", "descending", "nulls_first"},
    "SortItem": {"this", "desc", "descending", "nulls_first"},
    "Group": {"expressions", "all"},
    "GroupBy": {"expressions", "all"},
}


def _walk_ast(value: Any, path: str) -> Iterator[tuple[dict, str]]:
    """Visit AST nodes in deterministic order, including nested expressions."""
    if isinstance(value, list):
        for i, element in enumerate(value):
            yield from _walk_ast(element, f"{path}[{i}]")
    elif isinstance(value, dict):
        if isinstance(value.get("type"), str) and isinstance(value.get("args"), dict):
            yield value, path
            for key, child in value["args"].items():
                yield from _walk_ast(child, f"{path}.args.{key}")
        else:
            for key, child in value.items():
                yield from _walk_ast(child, f"{path}.{key}")


def diagnose_request(
    request: AnalysisRequest,
    *,
    compilation_error: TransformError | UnsupportedCypherError | None = None,
) -> tuple[dict, ...]:
    """Return all known AST-level issues plus the first sequential compiler error.

    If compilation_error is supplied by a caller that already attempted
    compilation, the compiler is NOT invoked twice.
    """
    diagnostics: list[dict] = []
    clauses = request.clauses

    def record(index: int | None, kind: str | None, code: str, message: str,
               path: str | None = None) -> None:
        diagnostics.append({
            "request_id": request.request_id,
            "query": request.query_text,
            "clause_index": index,
            "clause_type": kind,
            "code": code,
            "error": message,
            "ast_path": path,
        })

    if not clauses:
        record(None, None, "EMPTY_QUERY", "Query contains no clauses")
    elif clauses[-1]["type"] != "Return":
        record(None, None, "MISSING_FINAL_RETURN", "Only queries ending in RETURN are supported")

    for index, clause in enumerate(clauses, start=1):
        kind = clause["type"]
        path = f"clauses[{index - 1}]"
        if kind not in _SUPPORTED_CLAUSES:
            record(index, kind, "UNSUPPORTED_CLAUSE",
                   f"Clause {kind!r} is not implemented", path)
        if kind == "Return" and index != len(clauses):
            record(index, kind, "RETURN_NOT_FINAL", "RETURN must be the final clause", path)

        for node, node_path in _walk_ast(clause, path):
            node_kind = node["type"]
            args = node["args"]
            if node_kind in _UNSUPPORTED_EXPRESSIONS:
                record(index, kind, "UNSUPPORTED_EXPRESSION",
                       f"Expression {node_kind!r} is not implemented", node_path)
            if node_kind == "FunctionCall" and isinstance(args.get("name"), str):
                name = args["name"]
                if name.lower() in ("startswith", "endswith", "contains"):
                    operator = {
                        "startswith": "STARTS WITH", "endswith": "ENDS WITH", "contains": "CONTAINS"
                    }[name.lower()]
                    record(index, kind, "NONSTANDARD_STRING_FUNCTION",
                           f"{name}() is not a built-in Cypher string predicate; use {operator}", node_path)
            if node_kind in _ALLOWED_ARGS:
                for name, value in args.items():
                    if name not in _ALLOWED_ARGS[node_kind] and value is not None:
                        record(index, kind, "UNSUPPORTED_AST_FIELD",
                               f"{node_kind}.{name} is not implemented", f"{node_path}.args.{name}")
            if node_kind == "Match":
                if args.get("optional") not in (None, False):
                    record(index, kind, "UNSUPPORTED_OPTIONAL_MATCH",
                           "OPTIONAL MATCH is not implemented", node_path)
                if args.get("hints") or args.get("search"):
                    record(index, kind, "UNSUPPORTED_MATCH_OPTIONS",
                           "MATCH hints/search are not implemented", node_path)
            elif node_kind == "PathPattern" and args.get("variable") is not None:
                record(index, kind, "UNSUPPORTED_NAMED_PATH",
                       "Named path variables are not implemented", node_path)
            elif node_kind == "RelationshipPattern":
                for flag in ("min_hops", "max_hops", "variable_length", "memgraph_quantifier",
                             "memgraph_weight_expr", "memgraph_total_weight"):
                    if args.get(flag) is not None:
                        record(index, kind, "UNSUPPORTED_RELATIONSHIP_FEATURE",
                               f"RelationshipPattern.{flag} is not implemented",
                               f"{node_path}.args.{flag}")
                if args.get("direction") not in (None, "OUTGOING", "INCOMING", "BOTH"):
                    record(index, kind, "UNSUPPORTED_RELATIONSHIP_DIRECTION",
                           f"Relationship direction {args['direction']!r} is not implemented", node_path)
            elif node_kind == "LabelExpression" and args.get("expression") is not None:
                record(index, kind, "UNSUPPORTED_LABEL_EXPRESSION",
                       "Boolean label expressions are not implemented", node_path)
            elif node_kind in ("Ordered", "SortItem") and args.get("nulls_first") is not None:
                record(index, kind, "UNSUPPORTED_SORT_NULLS",
                       "Explicit NULLS FIRST/LAST is not implemented", node_path)
            elif node_kind == "Return" and args.get("where") is not None:
                record(index, kind, "UNSUPPORTED_RETURN_WHERE",
                       "RETURN WHERE is not implemented", node_path)

    if compilation_error is None:
        # Run once to surface sequential validation errors such as out-of-scope
        # references, type errors and deeply nested malformed expressions.
        from graph_query_analysis.compiler import compile_request
        try:
            compile_request(request)
        except (TransformError, UnsupportedCypherError) as exc:
            compilation_error = exc

    if compilation_error is not None:
        match = re.match(r"Clause (\d+) \(([^)]+)\):", str(compilation_error))
        index = int(match.group(1)) if match else None
        kind = match.group(2) if match else None
        message = str(compilation_error)
        if not any(item["error"] == message for item in diagnostics):
            record(index, kind, "COMPILATION_ERROR", message)

    return tuple(diagnostics)


def diagnose_requests(requests: list[AnalysisRequest] | tuple[AnalysisRequest, ...]) -> tuple[tuple[dict, ...], ...]:
    """Diagnose a batch, keeping one error tuple for each input request."""
    return tuple(diagnose_request(request) for request in requests)
