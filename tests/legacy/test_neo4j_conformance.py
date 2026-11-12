from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from cypherast.errors import ParseError


ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = Path(__file__).with_name("cypher_parser.py")
PARSER_SPEC = importlib.util.spec_from_file_location("project_cypher_parser", PARSER_PATH)
PARSER_MODULE = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(PARSER_MODULE)
EXECUTABLE = ROOT / "lean/.lake/build/bin" / (
    "lean_cypher.exe" if os.name == "nt" else "lean_cypher"
)
NEO4J_URI = os.getenv("LEAN_CYPHER_TEST_NEO4J_URI")

GRAPH = {
    "nodes": [
        {"id": 0, "labels": ["A", "Extra"]},
        {"id": 1, "labels": ["B"]},
        {"id": 2, "labels": ["C"]},
        {"id": 3, "labels": ["C"]},
    ],
    "relationships": [
        {"id": 0, "source": 0, "target": 1, "relType": "R"},
        {"id": 1, "source": 0, "target": 1, "relType": "R"},
        {"id": 2, "source": 1, "target": 2, "relType": "S"},
        {"id": 3, "source": 1, "target": 0, "relType": "S"},
        {"id": 4, "source": 2, "target": 2, "relType": "LOOP"},
    ],
}

N0, N1, N2, N3 = ({"node": i} for i in range(4))
R0, R1, LOOP = ({"relationship": i} for i in (0, 1, 4))

CASES = (
    ("MATCH (a:A:Extra) RETURN a", ["a"], [[N0]]),
    ("MATCH (a:A)-[r:R]->(b:B) RETURN r, b, a", ["r", "b", "a"],
     [[R0, N1, N0], [R1, N1, N0]]),
    ("MATCH (b:B)<-[:R]-(a:A) RETURN b", ["b"], [[N1], [N1]]),
    ("MATCH (a)-[:R]-(b) RETURN a, b", ["a", "b"],
     [[N0, N1], [N0, N1], [N1, N0], [N1, N0]]),
    ("MATCH (a:C)-[r:LOOP]-(a) RETURN r, a", ["r", "a"], [[LOOP, N2]]),
    ("MATCH (:A)-[:R]->(b)-[:S]->(:C) RETURN b", ["b"], [[N1], [N1]]),
    ("MATCH (:A)-[:R]->(b)-[:S]->(:C) RETURN DISTINCT b", ["b"], [[N1]]),
    ("MATCH (:A)-[:R]->()-[:S]->()-[:LOOP]->(c:C) RETURN c", ["c"], [[N2], [N2]]),
    ("MATCH (a:A)-[:R]->()-[:S]->(a) RETURN a", ["a"], [[N0], [N0]]),
    ("MATCH (:A)-[:R]->(b:B), (:A)-[:R]->(b) RETURN b", ["b"], [[N1], [N1]]),
    ("MATCH (:A)-[:R]->(b:B) MATCH (:A)-[:R]->(b) RETURN b", ["b"], [[N1]] * 4),
    ("MATCH (:A)-[r:R]->(b) MATCH (:A)-[r:R]->(b) RETURN r", ["r"], [[R0], [R1]]),
    ("MATCH (a:A), (c:C) RETURN a, c", ["a", "c"], [[N0, N2], [N0, N3]]),
    ("MATCH (:A)-[:R|S]->(b) RETURN b", ["b"], [[N1], [N1]]),
    ("MATCH (a:A)-[:S|LOOP]->() RETURN a", ["a"], []),
    ("MATCH (a:A)-[r:R]->() WITH a AS kept MATCH (kept)-[:R]->(b) RETURN kept, b",
     ["kept", "b"], [[N0, N1]] * 4),
    ("MATCH (a:A)-[r:R]->() WITH DISTINCT a AS kept MATCH (kept)-[:R]->(b) RETURN kept, b",
     ["kept", "b"], [[N0, N1]] * 2),
    ("MATCH (a:A) WITH a AS kept MATCH (a:C) RETURN kept, a",
     ["kept", "a"], [[N0, N2], [N0, N3]]),
    ("MATCH (:A)-[r:R]->() WITH r AS link MATCH (:A)-[link]->(:B) RETURN link",
     ["link"], [[R0], [R1]]),
    ("WITH 1 AS a, 2 AS b WITH a AS b, b AS a RETURN b, a", ["b", "a"], [[1, 2]]),
    ("MATCH (a:A), (b:C) WITH DISTINCT a, b RETURN 0 AS constant", ["constant"], [[0], [0]]),
)


def evaluate(graph, queries, mode="eval"):
    parser = PARSER_MODULE.cypherast_parser()
    with TemporaryDirectory() as directory:
        graph_path = Path(directory) / "graph.json"
        query_path = Path(directory) / "queries.json"
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        parser.build_lean_request(queries, [{}] * len(queries), path=query_path)
        return subprocess.run(
            [EXECUTABLE, mode, graph_path, query_path],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )


def row_bag(rows):
    return Counter(json.dumps(row, sort_keys=True) for row in rows)


class Neo4jParserTests(unittest.TestCase):
    def test_request_writer_requires_a_whole_schema_for_each_query(self):
        parser = PARSER_MODULE.cypherast_parser()
        query = "MATCH (n:Person) RETURN n"
        schema = {"node_props": {"Person": []}}
        requests = parser.build_lean_request([query], [json.dumps(schema)], write_file=False)
        self.assertEqual(requests[0]["schema"], schema)
        with self.assertRaisesRegex(ValueError, "one schema per query"):
            parser.build_lean_request([query, query], [schema], write_file=False)
        with self.assertRaisesRegex(ValueError, "one schema per query"):
            parser.build_lean_request([query], json.dumps(schema), write_file=False)

    def test_two_arrowheads_fail_before_serialization(self):
        parser = PARSER_MODULE.cypherast_parser()
        for pattern in ("(a)<-[:R]->(b)", "(a)<-->(b)"):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ParseError, "two arrowheads"):
                    parser.parse_to_ast([f"MATCH {pattern} RETURN a"])

        parser.parse_to_ast(["MATCH (a)-[:R]-(b) RETURN a", "RETURN '<-[:R]->'"])


@unittest.skipUnless(EXECUTABLE.is_file(), "Run lake build in lean/ first")
class LeanEvaluationTests(unittest.TestCase):
    def test_fixed_length_match_bags(self):
        result = evaluate(GRAPH, [query for query, _, _ in CASES])
        self.assertEqual(result.returncode, 0, result.stderr)
        tables = json.loads(result.stdout)
        symbolic = evaluate(GRAPH, [query for query, _, _ in CASES], mode="eval-symbolic")
        self.assertEqual(symbolic.returncode, 0, symbolic.stderr)
        self.assertEqual(json.loads(symbolic.stdout), tables)
        self.assertEqual(len(tables), len(CASES))
        for (query, columns, rows), table in zip(CASES, tables):
            with self.subTest(query=query):
                self.assertEqual(table["columns"], columns)
                self.assertEqual(row_bag(table["rows"]), row_bag(rows))

    def test_bag_counts_depend_on_where_distinct_is_applied(self):
        graph = {
            "nodes": GRAPH["nodes"],
            "relationships": [
                {"id": 0, "source": 0, "target": 1, "relType": "R"},
                {"id": 1, "source": 0, "target": 1, "relType": "R"},
                {"id": 2, "source": 1, "target": 2, "relType": "S"},
                {"id": 3, "source": 1, "target": 2, "relType": "S"},
                {"id": 4, "source": 1, "target": 3, "relType": "S"},
            ],
        }
        first = "MATCH (:A)-[:R]->(b:B) "
        second = "MATCH (b)-[:S]->(c:C) "
        cases = [
            (first + second + "RETURN c", [[N2]] * 4 + [[N3]] * 2),
            (first + "WITH b " + second + "RETURN c", [[N2]] * 4 + [[N3]] * 2),
            (first + "WITH DISTINCT b " + second + "RETURN c", [[N2]] * 2 + [[N3]]),
            (first + second + "RETURN DISTINCT c", [[N2], [N3]]),
            ("MATCH (:A)-[:R]->(b:B), (b)-[:S]->(c:C) RETURN c", [[N2]] * 4 + [[N3]] * 2),
        ]
        queries = [query for query, _ in cases]
        result = evaluate(graph, queries)
        self.assertEqual(result.returncode, 0, result.stderr)
        tables = json.loads(result.stdout)
        symbolic = evaluate(graph, queries, mode="eval-symbolic")
        self.assertEqual(symbolic.returncode, 0, symbolic.stderr)
        self.assertEqual(json.loads(symbolic.stdout), tables)
        for table, (query, rows) in zip(tables, cases, strict=True):
            with self.subTest(query=query):
                self.assertEqual(row_bag(table["rows"]), row_bag(rows))

    def test_mandatory_match_sums_complete_extensions_over_input_rows(self):
        graph = {
            "nodes": [
                *({"id": i, "labels": ["B"]} for i in range(4)),
                {"id": 4, "labels": ["RTarget"]},
                {"id": 5, "labels": ["STarget"]},
            ],
            "relationships": [
                {"id": 0, "source": 0, "target": 4, "relType": "R"},
                {"id": 1, "source": 0, "target": 4, "relType": "R"},
                {"id": 2, "source": 0, "target": 5, "relType": "S"},
                {"id": 3, "source": 1, "target": 4, "relType": "R"},
                {"id": 4, "source": 2, "target": 4, "relType": "R"},
                {"id": 5, "source": 2, "target": 5, "relType": "S"},
                {"id": 6, "source": 3, "target": 5, "relType": "S"},
            ],
        }
        result = evaluate(graph, ["MATCH (b:B) MATCH (b)-[:R]->(), (b)-[:S]->() RETURN b"])
        self.assertEqual(result.returncode, 0, result.stderr)
        table, = json.loads(result.stdout)
        self.assertEqual(row_bag(table["rows"]), row_bag([[N0], [N0], [N2]]))

    def test_with_scope_is_checked_even_when_no_rows_reach_the_clause(self):
        for query, message in (
            ("MATCH (a:A) WITH a AS kept RETURN a", "unbound variable a"),
            ("MATCH (a:Absent) WITH a AS kept RETURN a", "unbound variable a"),
            ("MATCH (a:A) WITH a AS kept RETURN a.name", "unbound variable a"),
            ("MATCH (a:A) WITH a AS kept, kept AS sibling RETURN sibling", "unbound variable kept"),
            ("MATCH (a:A) WITH a AS kept MATCH (b) WHERE a = b RETURN b", "unbound variable a"),
            ("MATCH (a:A) WITH a AS kept, a AS kept RETURN kept", "duplicate result column kept"),
            ("WITH 1 AS kept MATCH (kept) RETURN kept", "incompatible binding kinds"),
        ):
            with self.subTest(query=query):
                result = evaluate(GRAPH, [query])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stderr)

    def test_with_modifiers_are_not_silently_ignored(self):
        result = evaluate(GRAPH, ["WITH 1 AS kept WHERE kept > 0 RETURN kept"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".args.where: unsupported field", result.stderr)

    def test_rejects_dangling_endpoint(self):
        graph = {"nodes": GRAPH["nodes"][:1], "relationships": GRAPH["relationships"]}
        result = evaluate(graph, ["MATCH (a:A) RETURN a"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("endpoint absent", result.stderr)

    def test_property_maps_null_predicates_and_projection(self):
        graph = {
            "nodes": [
                {"id": 0, "labels": ["Account"], "properties": {"balance": 906.83}},
                {"id": 1, "labels": ["Institute"], "properties": {"name": "State", "codes": [1, 2]}},
                {"id": 2, "labels": ["Institute"]},
            ],
            "relationships": [
                {"id": 0, "source": 0, "target": 1, "relType": "FROM", "properties": {"active": True}},
                {"id": 1, "source": 0, "target": 1, "relType": "FROM", "properties": {"active": False}},
                {"id": 2, "source": 0, "target": 2, "relType": "FROM", "properties": {"active": True}},
            ],
        }
        queries = [
            "MATCH (a:Account {balance: 906.83})-[r:FROM {active: true}]->(i) "
            "WHERE i.name STARTS WITH 'S' AND a.balance > 500 "
            "RETURN i.name AS name, a.balance, r.active AS active, i.codes AS codes, i.missing AS missing",
            "MATCH (:Account)-[:FROM]->(i) WHERE i.name = null RETURN i",
            "MATCH (:Account)-[:FROM]->(i) WHERE i.name IS NULL RETURN i",
            "MATCH (:Account)-[:FROM]->(i) RETURN DISTINCT i.missing AS missing",
            "RETURN 9007199254740993 = 9007199254740992.0 AS equal, 1e-12 AS small",
            "MATCH (a:Account) WITH a.balance AS balance, a AS kept "
            "RETURN kept.balance AS original, balance",
        ]
        result = evaluate(graph, queries)
        self.assertEqual(result.returncode, 0, result.stderr)
        symbolic = evaluate(graph, queries, mode="eval-symbolic")
        self.assertEqual(symbolic.returncode, 0, symbolic.stderr)
        self.assertEqual(json.loads(symbolic.stdout), json.loads(result.stdout))
        self.assertEqual(json.loads(result.stdout), [
            {"columns": ["name", "a.balance", "active", "codes", "missing"],
             "rows": [["State", 906.83, True, [1, 2], None]]},
            {"columns": ["i"], "rows": []},
            {"columns": ["i"], "rows": [[{"node": 2}]]},
            {"columns": ["missing"], "rows": [[None]]},
            {"columns": ["equal", "small"], "rows": [[False, 1e-12]]},
            {"columns": ["original", "balance"], "rows": [[906.83, 906.83]]},
        ])

    def test_symbolic_interpretation_preserves_evaluation_errors(self):
        for query, message in (
            ("MATCH (a:A) WHERE a > 1 RETURN a", "unsupported comparison"),
            ("WITH 1 AS a RETURN a.age AS age", "a is not a graph entity"),
        ):
            with self.subTest(query=query):
                concrete = evaluate(GRAPH, [query])
                symbolic = evaluate(GRAPH, [query], mode="eval-symbolic")
                self.assertEqual(concrete.returncode, 1, concrete.stdout)
                self.assertIn(message, concrete.stderr)
                self.assertEqual(symbolic.returncode, concrete.returncode)
                self.assertEqual(symbolic.stderr, concrete.stderr)

    def test_proved_bounds_allow_mixed_numeric_values_and_preserve_errors(self):
        graph = {"nodes": [
            {"id": 0, "labels": ["Person"], "properties": {"age": 20.5}},
            {"id": 1, "labels": ["Person"], "properties": {"age": 20}},
            {"id": 2, "labels": ["Person"]},
        ], "relationships": []}
        impossible = "MATCH (p:Person) WHERE p.age > 30 AND p.age < 20 RETURN p"
        possible = "MATCH (p:Person) WHERE p.age > 20 AND p.age < 21 RETURN p"
        result = evaluate(graph, [impossible, possible], mode="eval-symbolic")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([table["rows"] for table in json.loads(result.stdout)], [[], [[N0]]])
        graph["nodes"].append({"id": 3, "labels": ["Person"], "properties": {"age": "unknown"}})
        failed = evaluate(graph, [impossible], mode="eval-symbolic")
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("unsupported comparison", failed.stderr)


@unittest.skipUnless(NEO4J_URI, "Set LEAN_CYPHER_TEST_NEO4J_URI to run against Neo4j")
@unittest.skipUnless(EXECUTABLE.is_file(), "Run lake build in lean/ first")
class LiveNeo4jConformanceTests(unittest.TestCase):
    def test_neo4j_rejects_two_arrowheads(self):
        from neo4j import GraphDatabase
        from neo4j.exceptions import CypherSyntaxError

        with GraphDatabase.driver(NEO4J_URI, auth=self.auth()) as driver, driver.session() as session:
            for pattern in ("(a)<-[:R]->(b)", "(a)<-->(b)"):
                with self.subTest(pattern=pattern), self.assertRaises(CypherSyntaxError):
                    session.run(f"CYPHER 5 EXPLAIN MATCH {pattern} RETURN a").consume()

    @staticmethod
    def auth():
        username = os.getenv("LEAN_CYPHER_TEST_NEO4J_USER", "neo4j")
        password = os.getenv("LEAN_CYPHER_TEST_NEO4J_PASSWORD")
        return (username, password) if password is not None else None

    def test_lean_matches_neo4j_cypher_5(self):
        from neo4j import GraphDatabase
        from neo4j.graph import Node

        result = evaluate(GRAPH, [query for query, _, _ in CASES])
        self.assertEqual(result.returncode, 0, result.stderr)
        tables = json.loads(result.stdout)
        self.assertEqual(len(tables), len(CASES))
        with GraphDatabase.driver(NEO4J_URI, auth=self.auth()) as driver, driver.session() as session:
            with session.begin_transaction() as tx:
                count = tx.run("CYPHER 5 MATCH (n) RETURN count(n)").single(strict=True)[0]
                self.assertEqual(count, 0, "Use an empty test database")
                patterns = [
                    f"(n{n['id']}:{':'.join(n['labels'])} {{leanId: {n['id']}}})"
                    for n in GRAPH["nodes"]
                ]
                patterns.extend(
                    f"(n{r['source']})-[:{r['relType']} {{leanId: {r['id']}}}]->(n{r['target']})"
                    for r in GRAPH["relationships"]
                )
                tx.run("CYPHER 5 CREATE " + ", ".join(patterns)).consume()
                for (query, _, _), lean_table in zip(CASES, tables):
                    with self.subTest(query=query):
                        records = tx.run("CYPHER 5 " + query)
                        self.assertEqual(records.keys(), lean_table["columns"])
                        neo4j_rows = [
                            [{"node" if isinstance(cell, Node) else "relationship": cell["leanId"]}
                             for cell in record.values()]
                            for record in records
                        ]
                        self.assertEqual(row_bag(neo4j_rows), row_bag(lean_table["rows"]))
                tx.rollback()
