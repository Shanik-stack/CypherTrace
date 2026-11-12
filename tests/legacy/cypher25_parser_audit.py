"""Audit CypherAST's Neo4j 25 parser before queries reach graph_query_analysis.

Place next to ``src/pipeline/lean_request/cypher_parser.py``.
Run from the repository root:

    PYTHONPATH=src python -m pipeline.lean_request.cypher25_parser_audit

PowerShell:

    $env:PYTHONPATH = "src"
    python -m pipeline.lean_request.cypher25_parser_audit

No Cypher is executed. Every probe passes through YOUR existing
``cypherast_parser.parse_to_ast()`` and ``ast_to_dict()`` methods.

A successful parse does NOT automatically imply that CypherAST preserved all
semantics, or that the downstream symbolic analyzer supports the construct.
Inspect the emitted AST JSON for successful advanced-mode probes.
"""

import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from cypher_parser import cypherast_parser


@dataclass(frozen=True)
class Probe:
    feature: str
    query: str
    # For critical syntax, verify the renderer or AST manually; do not infer
    # semantic correctness from a successful parse alone.
    inspect_ast: bool = False


# Features chosen from Neo4j Cypher 25 documentation. This list is an audit,
# NOT an assumption that any particular feature is supported by CypherAST.
PROBES = (
    Probe("basic_match", "MATCH (a:Person) RETURN a"),
    Probe("two_hop_match", "MATCH (a)-[:R]->(b)-[:S]->(c) RETURN c"),
    Probe("multiple_paths", "MATCH (a)-[:R]->(b), (b)-[:S]->(c) RETURN c"),
    Probe("undirected_match", "MATCH (a)-[:R]-(b) RETURN b"),
    Probe("optional_match", "MATCH (a) OPTIONAL MATCH (a)-[:R]->(b) RETURN b"),
    Probe("with_distinct", "MATCH (a)-[:R]->(b) WITH DISTINCT b RETURN b"),
    Probe("global_aggregation", "MATCH (a) RETURN avg(a.value) AS mean"),
    Probe("inline_node_where", "MATCH (a:Person WHERE a.age > 30) RETURN a"),
    Probe("legacy_variable_length", "MATCH p = (a)-[:R*1..3]->(b) RETURN p"),
    Probe("quantified_relationship", "MATCH p = (a)-[:R]->{1,3}(b) RETURN p", True),
    Probe("quantified_path_pattern", "MATCH p = (a)((x)-[:R]->(y)){1,3}(b) RETURN p", True),
    Probe("different_relationships", "MATCH DIFFERENT RELATIONSHIPS (a)-[:R]->(b) RETURN b", True),
    Probe("repeatable_elements", "MATCH REPEATABLE ELEMENTS (a)-[:R]->(b) RETURN b", True),
    Probe("path_mode_walk", "MATCH p = WALK (a)-[:R]->(b) RETURN p", True),
    Probe("path_mode_trail", "MATCH p = TRAIL (a)-[:R]->(b) RETURN p", True),
    Probe("path_mode_acyclic", "MATCH p = ACYCLIC (a)-[:R]->(b)-[:S]->(c) RETURN p", True),
    Probe("any_path_selector", "MATCH p = ANY (a)-[:R]->{1,3}(b) RETURN p", True),
    Probe("shortest_path_selector", "MATCH p = SHORTEST 1 (a)-[:R]->{1,3}(b) RETURN p", True),
    Probe("all_shortest_selector", "MATCH p = ALL SHORTEST (a)-[:R]->{1,3}(b) RETURN p", True),
    Probe("exists_subquery", "MATCH (a) WHERE EXISTS { MATCH (a)-[:R]->(b) } RETURN a"),
    Probe("count_subquery", "MATCH (a) RETURN COUNT { MATCH (a)-[:R]->(b) } AS n"),
    Probe("collect_subquery", "MATCH (a) RETURN COLLECT { MATCH (a)-[:R]->(b) RETURN b } AS bs"),
    Probe("call_subquery", "CALL { MATCH (a:Person) RETURN a } RETURN a"),
    Probe("let_clause", "MATCH (a) LET age = a.age RETURN age"),
    Probe("for_filter_clause", "FOR x IN [1, 2] FILTER x > 1 RETURN x"),
)


def run_audit(output: Path | None = None) -> list[dict]:
    parser = cypherast_parser()
    results = []
    print(f"cypherast version: {version('cypherast')}\n")
    print(f"{'FEATURE':30} {'PARSE':10} DETAIL")
    print("-" * 110)

    for probe in PROBES:
        try:
            ast_objects = parser.parse_to_ast([probe.query])
            ast_dicts = parser.ast_to_dict(ast_objects, write_file=False)
            parsed_ast = ast_dicts[0]
        except Exception as exc:
            # Rejected means the installed parser did not accept this query.
            # Independently verify valid Cypher syntax before labeling it a
            # library limitation rather than an invalid test query.
            error = str(exc).splitlines()[0]
            record = {
                'feature': probe.feature,
                'parse': 'REJECTED',
                'query': probe.query,
                'error': error,
            }
            print(f"{probe.feature:30} {'REJECTED':10} {error}")
        else:
            record = {
                'feature': probe.feature,
                'parse': 'ACCEPTED',
                'query': probe.query,
                'ast': parsed_ast,
                'requires_semantic_inspection': probe.inspect_ast,
            }
            marker = 'CHECK AST' if probe.inspect_ast else ''
            print(f"{probe.feature:30} {'ACCEPTED':10} {marker}")
        results.append(record)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(f"\nWrote full report to {output}")

    return results


if __name__ == '__main__':
    run_audit(Path('cypher25_parser_report.json'))
