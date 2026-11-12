import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from cypher_parser import cypherast_parser


ROOT = Path(__file__).resolve().parents[2]
TWO_HOP_QUERY = "MATCH (a:Person)-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person) RETURN b"
EXECUTABLE = ROOT / "lean/.lake/build/bin" / (
    "lean_cypher.exe" if os.name == "nt" else "lean_cypher"
)


@unittest.skipUnless(EXECUTABLE.is_file(), "Run lake build in lean/ first")
class LeanDecoderTests(unittest.TestCase):
    def decode(self, path):
        return subprocess.run(
            [EXECUTABLE, path], capture_output=True, encoding="utf-8", check=False
        )

    def decode_modified_fixture(self, edit_clauses):
        ast = self.two_hop_ast()
        edit_clauses(ast[0]["args"]["this"]["args"]["clauses"])
        return self.decode_payload(ast)

    def decode_payload(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ast.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return self.decode(path)

    def analysis_request(self):
        return {
            "protocol_version": 1,
            "request_id": "two_hop",
            "dialect": "neo4j25",
            "query_text": TWO_HOP_QUERY,
            "query_ast": self.two_hop_ast(),
            "schema": {"node_props": {"Person": []}},
            "parameters": {},
        }

    def two_hop_ast(self):
        parser = cypherast_parser()
        return parser.ast_to_dict(parser.parse_to_ast([TWO_HOP_QUERY]), write_file=False)

    def purchase_request(self, query, amount_type="FLOAT"):
        schema = {
            "node_props": {
                "Purchase": [{"property": "amount", "type": amount_type}],
                "DeliveryAddress": [{"property": "state", "type": "STRING"}],
            },
            "relationships": [
                {"start": "Purchase", "type": "DELIVERED_AT", "end": "DeliveryAddress"},
            ],
        }
        return cypherast_parser().build_lean_request([query], [schema], write_file=False)

    def test_unified_request_analyzes_schema_without_a_graph(self):
        request = self.analysis_request()
        result = self.decode_payload([request])
        self.assertEqual(result.returncode, 0, result.stderr)
        report, = json.loads(result.stdout)
        self.assertEqual(report["schema_interpretation"], "observational")
        match, returning = report["clauses"]
        path, = match["paths"]
        self.assertEqual(path["nodes"][0]["labels"], {"Person": "listed"})
        self.assertEqual([hop["topology_status"] for hop in path["hops"]], ["not_listed"] * 2)
        self.assertEqual(returning["columns"], [{"name": "b", "expression": {"variable": "b"}}])

    def test_request_reports_explicit_assumptions_separately_from_catalog_and_proofs(self):
        request = self.analysis_request()
        fact = {
            "entity": "node", "label_or_type": "Person", "property": "name",
            "evidence": "enforced", "source": "test constraint",
        }
        request["schema"]["assumptions"] = {"required_properties": [fact]}
        result = self.decode_payload([request])
        self.assertEqual(result.returncode, 0, result.stderr)
        report, = json.loads(result.stdout)
        self.assertEqual(report["schema_interpretation"], "observational")
        self.assertEqual(report["schema_assumptions"], {"required_properties": [fact]})
        self.assertEqual(report["schema_assumptions_used"], [])

    def test_request_rejects_invalid_required_property_assumptions(self):
        fact = {
            "entity": "node", "label_or_type": "Person", "property": "name",
            "evidence": "declared", "source": "test contract",
        }
        for invalid in ({"evidence": "observed"}, {"source": " "}, {"when": "active"}):
            with self.subTest(invalid=invalid):
                request = self.analysis_request()
                request["schema"]["assumptions"] = {"required_properties": [fact | invalid]}
                result = self.decode_payload([request])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("schema.assumptions: required_properties[0]", result.stderr)

    def test_request_uses_topology_and_property_schema(self):
        query = (
            "MATCH (p:Purchase)-[:DELIVERED_AT]->(d:DeliveryAddress {state: 'Montana'}) "
            "RETURN avg(p.amount) AS average"
        )
        result = self.decode_payload(self.purchase_request(query))
        self.assertEqual(result.returncode, 0, result.stderr)
        report, = json.loads(result.stdout)
        match, returning = report["clauses"]
        path, = match["paths"]
        hop, = path["hops"]
        self.assertEqual(hop["matching_schema_signatures"], [
            {"start": "Purchase", "type": "DELIVERED_AT", "end": "DeliveryAddress"},
        ])
        constraint, = path["nodes"][1]["property_constraints"]
        self.assertEqual(constraint["property"]["listed_types"], ["STRING"])
        self.assertEqual(constraint["equals"], "Montana")
        self.assertEqual(returning["aggregation"], "avg")
        self.assertEqual(returning["argument_types_in_evidence"], ["FLOAT"])
        self.assertEqual(returning["argument_assessment"], "compatible_type_evidence")
        self.assertEqual(report["symbolic_bag"]["result"], {"kind": "aggregate_input_only", "bag": 1})

    def test_direction_and_aliased_property_evidence_come_from_schema(self):
        query = (
            "MATCH (p:Purchase)<-[:DELIVERED_AT]-(d:DeliveryAddress) "
            "WITH p.amount AS amount RETURN avg(amount) AS average"
        )
        for schema_type, assessment in (
            ("FLOAT", "compatible_type_evidence"),
            ("STRING", "possible_type_mismatch"),
        ):
            with self.subTest(schema_type=schema_type):
                result = self.decode_payload(self.purchase_request(query, schema_type))
                self.assertEqual(result.returncode, 0, result.stderr)
                report, = json.loads(result.stdout)
                match, _, returning = report["clauses"]
                self.assertEqual(match["paths"][0]["hops"][0]["topology_status"], "not_listed")
                self.assertEqual(returning["argument_types_in_evidence"], [schema_type])
                self.assertEqual(returning["argument_assessment"], assessment)

    def test_proved_findings_compose_matching_filtering_and_projection(self):
        query = (
            "MATCH (a:A)-[:R]->(b:B), (a)-[:S]->(c:C) WHERE c.rank > 0 "
            "WITH DISTINCT b AS kept MATCH (kept)-[:T]->(d) RETURN d"
        )
        parser = cypherast_parser()
        payload = parser.build_lean_request([query, query], [
            {},
            {"relationships": [{"start": "A", "type": "R", "end": "B"}]},
        ], write_file=False)
        result = self.decode_payload(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second = json.loads(result.stdout)
        self.assertEqual(first["result_proof_coverage"], "row_result")
        self.assertEqual(first["proved_findings"], second["proved_findings"])
        symbolic = first["symbolic_bag"]
        self.assertEqual(symbolic, second["symbolic_bag"])
        stages = symbolic["stages"]
        self.assertEqual([stage["operation"]["kind"] for stage in stages], [
            "singleton_empty_row", "extend_witnesses", "filter_where_true", "project",
            "distinct", "extend_witnesses", "project",
        ])
        self.assertEqual([stage["input"] for stage in stages[1:]], list(range(6)))
        self.assertEqual([stage["clause_index"] for stage in stages[1:]], [0, 0, 1, 1, 2, 3])
        first_match = stages[1]["operation"]
        self.assertEqual(first_match["all_different_relationships"], ["p0.r0", "p1.r0"])
        first_edge = first_match["paths"][0]["steps"][0]["relationship"]
        self.assertEqual(first_edge, {
            "witness": "p0.r0", "binding": None, "direction": "outgoing",
            "from": "p0.n0", "to": "p0.n1", "types": ["R"], "properties": {},
        })
        self.assertEqual(stages[3]["scope"], [{"name": "kept", "kind": "node"}])
        self.assertEqual(stages[3]["operation"]["columns"], [
            {"name": "kept", "expression": {"variable": "b"}},
        ])
        self.assertEqual(stages[5]["operation"]["paths"][0]["start"]["binding"], "kept")
        self.assertEqual(symbolic["result"], {"kind": "row_result", "bag": 6})
        self.assertNotEqual(first["clauses"], second["clauses"])
        findings = first["proved_findings"]
        self.assertEqual(findings[0]["kind"], "result_row_derivation")
        bag, = [item for item in findings if item["kind"] == "result_bag_multiplicity"]
        self.assertEqual(bag["proof_rule"], "Cypher.Plan.eval_result_bag")
        self.assertEqual(bag["symbolic_proof_rule"], "Cypher.Plan.symbolic_result_bag")
        self.assertEqual(bag["applies_to"], "query_result")
        self.assertEqual(bag["distinct_rule"], "keep_first_representative_at_each_distinct_stage")
        matches = [item for item in findings if item["kind"] == "mandatory_match"]
        self.assertEqual([item["clause_index"] for item in matches], [0, 2])
        self.assertEqual(matches[0]["required_paths"], 2)
        self.assertEqual(matches[0]["relationship_occurrences"], 2)
        self.assertEqual(matches[0]["applies_to"], "clause_output")
        self.assertEqual(matches[0]["path_requirement"], "all_paths_for_each_contribution")
        self.assertEqual(matches[0]["cardinality_rule"], "sum_over_input_occurrences")
        self.assertEqual(matches[0]["count_proof_rule"], "Cypher.BagSemantics.matchClause_count")
        self.assertEqual(matches[0]["witness_multiplicity"], "once_per_legal_witness_per_input_occurrence")
        self.assertTrue(matches[0]["where_must_pass"])
        self.assertTrue(matches[0]["relationship_ids_distinct_within_clause"])
        self.assertEqual(matches[1]["reused_input_bindings"], ["kept"])
        projection, = [item for item in findings if item["kind"] == "distinct_projection"]
        self.assertEqual(projection["clause_index"], 1)
        self.assertEqual(projection["columns"], ["kept"])
        self.assertEqual(set(projection["names_removed_from_scope"]), {"a", "b", "c"})
        self.assertFalse(any(item["kind"] == "no_result_rows_on_success" for item in findings))

    def test_closed_where_emptiness_propagates_through_a_row_pipeline(self):
        parser = cypherast_parser()
        queries = [
            "MATCH (a) WITH a MATCH (b) WHERE NOT (1 = 1 AND null IS NULL) "
            "WITH DISTINCT b MATCH (c) RETURN c",
            "MATCH (a) WHERE null RETURN a",
            "MATCH (a) WHERE 1 = 1 RETURN a",
            "MATCH (a) WHERE false RETURN avg(a.amount) AS average",
        ]
        payload = parser.build_lean_request(queries, [{} for _ in queries], write_file=False)
        result = self.decode_payload(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)
        self.assertEqual(reports[-1]["result_proof_coverage"], "aggregate_input_only")
        composed, null_where, true_where, aggregate = [
            {item["kind"]: item for item in report["proved_findings"]}
            for report in reports
        ]
        self.assertEqual(composed["no_result_rows_on_success"]["blocking_clause_index"], 2)
        self.assertEqual(null_where["no_result_rows_on_success"]["blocking_clause_index"], 0)
        self.assertNotIn("no_result_rows_on_success", true_where)
        self.assertIn("no_row_can_pass_where", aggregate)
        self.assertNotIn("no_result_rows_on_success", aggregate)
        self.assertNotIn("result_row_derivation", aggregate)
        self.assertNotIn("result_bag_multiplicity", aggregate)

    def test_symbolic_goals_distinguish_contradictions_from_unknowns(self):
        queries = [
            "MATCH (p:Person) WHERE p.age > 30 AND p.age < 20 "
            "WITH p MATCH (q) RETURN q",
            "MATCH (p:Person) WHERE 30 < p.score AND 30 >= p.score RETURN p",
            "MATCH ()-[r:R]->(), ()-[r:R]->() RETURN r",
            "MATCH ()-[r:R]->()-[r:R]->() RETURN r",
            "MATCH ()-[r:R]->() MATCH ()-[r:R]->() RETURN r",
            "MATCH (p:Person) WHERE p.age > 30 AND p.score < 20 RETURN p",
            "MATCH (p:Person) WHERE p.age > 20 AND p.age < 21 RETURN p",
            "MATCH (p:Person) WHERE p.age >= 20 AND p.age <= 20 RETURN p",
            "MATCH (p:Person) WHERE p.age > 30 OR p.age < 20 RETURN p",
        ]
        schema = {"node_props": {"UnrelatedLabel": []}}
        payload = cypherast_parser().build_lean_request(queries, [schema] * len(queries), write_file=False)
        result = self.decode_payload(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)
        for index, report in enumerate(reports):
            with self.subTest(query=queries[index]):
                solved = [goal for goal in report["proof_goals"] if goal["status"] == "proved"]
                terminal = [item for item in report["proved_findings"]
                            if item["kind"] == "no_result_rows_on_success"]
                self.assertEqual(len(solved), int(index < 4))
                self.assertEqual(len(terminal), len(solved))
                self.assertEqual(report["schema_assumptions_used"], [])
                if solved:
                    goal, = solved
                    finding, = terminal
                    self.assertEqual(goal["proof_rule"], "Cypher.BagOperator.empty_of_constraints")
                    self.assertEqual(goal["stage_id"], finding["blocking_stage_id"])
                    self.assertEqual(goal["stage_id"], 2 if index < 2 else 1)
                else:
                    self.assertTrue(report["proof_goals"])
                    self.assertTrue(all(goal["status"] == "unknown" for goal in report["proof_goals"]))
        self.assertEqual(reports[1]["proof_goals"][1]["constraints"], [
            {"kind": "integer_endpoint_bound", "subject": {"owner": "p", "property": "score"},
             "comparison": ">", "endpoint": 30},
            {"kind": "integer_endpoint_bound", "subject": {"owner": "p", "property": "score"},
             "comparison": "<=", "endpoint": 30},
        ])
        self.assertEqual(reports[2]["proof_goals"][0]["constraints"], [
            {"kind": "all_different_relationship_bindings", "names": ["r", "r"]},
        ])

    def test_invalid_request_envelopes_are_rejected(self):
        for field, value in (
            ("protocol_version", 2),
            ("dialect", "neo4j25"),
            ("schema", "{"),
            ("parameters", {"unused": 42}),
            ("query_ast", []),
            ("query_ast", self.analysis_request()["query_ast"] * 2),
        ):
            with self.subTest(field=field):
                request = self.analysis_request()
                request[field] = value
                result = self.decode_payload([request])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"$[0].{field}:", result.stderr)

    def test_ast_errors_include_the_request_id(self):
        request = self.analysis_request()
        clauses = request["query_ast"][0]["args"]["this"]["args"]["clauses"]
        clauses[-1]["args"]["expressions"][0]["args"]["this"] = "missing"
        result = self.decode_payload([request])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("request two_hop: $[0].query_ast[0]", result.stderr)
        self.assertIn("unbound variable missing", result.stderr)

    def test_raw_cypherast_preserves_both_hops_and_projection(self):
        result = self.decode_payload(self.two_hop_ast())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Direction.outgoing"), 2)
        self.assertEqual(result.stdout.count('types := ["KNOWS"]'), 2)
        self.assertIn('Expr.variable "b"', result.stdout)
        self.assertIn('column := "b"', result.stdout)

    def test_missing_fields_do_not_remove_query_restrictions(self):
        def node_args(clauses):
            path = clauses[0]["args"]["pattern"]["args"]["paths"][0]
            return path["args"]["elements"][0]["args"]

        for get_args, field in (
            (node_args, "labels"),
            (lambda clauses: clauses[0]["args"], "where"),
            (lambda clauses: clauses[0]["args"], "optional"),
            (lambda clauses: clauses[-1]["args"], "distinct"),
        ):
            with self.subTest(field=field):
                result = self.decode_modified_fixture(lambda clauses: get_args(clauses).pop(field))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f".{field}:", result.stderr)

    def test_unsupported_or_malformed_modifiers_are_rejected(self):
        for clause_index, key, value in (
            (0, "where", {"type": "Where", "args": {}}),
            (0, "optional", True),
            (1, "limit", {"type": "Limit", "args": {}}),
            (1, "unrecognized_modifier", None),
        ):
            with self.subTest(key=key):
                result = self.decode_modified_fixture(
                    lambda clauses: clauses[clause_index]["args"].update({key: value})
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f".args.clauses[{clause_index}].args.{key}", result.stderr)

    def test_projection_requires_a_binding(self):
        def replace_return_variable(clauses):
            clauses[-1]["args"]["expressions"][0]["args"]["this"] = "missing"

        result = self.decode_modified_fixture(replace_return_variable)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unbound variable missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
