"""Graph-free reports checked against small, explicitly constructed witness bags."""
import unittest
from cypherast.errors import CypherastError

from graph_query_analysis.compiler import compile_request
from graph_query_analysis.evaluate import NodeRecord, RelationshipRecord, PropertyGraph, EvaluationError, evaluate_request
from graph_query_analysis.algebraic_translation import symbolic_trace
from graph_query_analysis.input import parse_requests
from graph_query_analysis.report import generate_report
from pipeline.parser import build_request


SCHEMA = {
    "node_props": {
        "BankAccount": [{"property": "balance", "type": "INTEGER"}],
        "FinancialInstitute": [{"property": "name", "type": "STRING"}],
    },
    "rel_props": {"FROM": []},
    "relationships": [{"start": "BankAccount", "type": "FROM", "end": "FinancialInstitute"}],
}
PREFIX = "MATCH (f:FinancialInstitute) WHERE f.name STARTS WITH 'S' MATCH (b:BankAccount)-[:FROM]->(f) "
TRIALS = [
    PREFIX + "RETURN avg(b.balance) AS average, count(b) AS total",
    PREFIX + "RETURN avg(b.balance) AS average, count(DISTINCT b) AS total",
    PREFIX + "WITH DISTINCT b RETURN avg(b.balance) AS average, count(*) AS total",
]


def request(query, schema=SCHEMA, parameters=None):
    return parse_requests(build_request(query, schema, parameters=parameters))[0]


def fixture():
    # Two different accounts share a balance. One account has parallel FROM edges.
    nodes = (
        NodeRecord('f', frozenset({'FinancialInstitute'}), {'name': 'Savings'}),
        NodeRecord('a', frozenset({'BankAccount'}), {'balance': 10}),
        NodeRecord('b', frozenset({'BankAccount'}), {'balance': 10}),
        NodeRecord('c', frozenset({'BankAccount'}), {'balance': 40}),
    )
    edges = tuple(RelationshipRecord(str(i), start, 'f', 'FROM')
                  for i, start in enumerate(('a', 'a', 'b', 'c')))
    return PropertyGraph(nodes, edges)


def result(query, graph=None, parameters=None):
    return evaluate_request(request(query, parameters=parameters), graph or fixture())[-1]


def findings(query, rule):
    report = generate_report(request(query))
    assert report.status == 'compiled', report.compilation_error
    return [i for section in report.semantic_sections for i in section.insights if i.rule_id == rule]


class ReportTests(unittest.TestCase):
    def test_distinct_node_and_property_reports_differ(self):
        identity = findings(TRIALS[1], 'AGG-DISTINCT-ARGUMENT')[0]
        values = findings(PREFIX + 'RETURN count(DISTINCT b.balance) AS total', 'AGG-DISTINCT-ARGUMENT')[0]
        self.assertIn('node identities', identity.explanation)
        self.assertNotIn('Equal property values from different nodes may collapse', identity.explanation)
        self.assertIn('Equal property values from different nodes may collapse', values.explanation)

    def test_identity_survives_aliases_and_node_valued_functions(self):
        for query in (
            PREFIX + 'WITH b AS account RETURN count(DISTINCT account) AS total',
            PREFIX + 'WITH head(collect(b)) AS account RETURN count(DISTINCT account) AS total',
        ):
            with self.subTest(query=query):
                self.assertIn('node identities', findings(query, 'AGG-DISTINCT-ARGUMENT')[0].explanation)

    def test_relationship_distinct_is_identity(self):
        query = 'MATCH (b)-[r:FROM]->(f) RETURN count(DISTINCT r) AS total'
        self.assertIn('relationship identities', findings(query, 'AGG-DISTINCT-ARGUMENT')[0].explanation)

    def test_reduction_requires_actual_prior_operator(self):
        for query in TRIALS[:2]:
            with self.subTest(query=query):
                self.assertEqual(findings(query, 'AGG-ELIGIBILITY-AFTER-REDUCTION'), [])
                self.assertTrue(findings(query, 'AGG-AVG-WEIGHTING'))
        self.assertTrue(findings(TRIALS[2], 'AGG-ELIGIBILITY-AFTER-REDUCTION'))
        self.assertEqual(findings(TRIALS[2], 'AGG-AVG-WEIGHTING'), [])

    def test_prior_grouping_is_named_as_grouping(self):
        query = PREFIX + 'WITH b, count(*) AS n RETURN sum(n) AS total'
        found = findings(query, 'AGG-ELIGIBILITY-AFTER-REDUCTION')
        self.assertTrue(found)
        self.assertTrue(all('DISTINCT' not in i.title for i in found))
        self.assertTrue(findings(query, 'AGG-PRIOR-AGGREGATE-VALUE-INFLUENCE'))

    def test_missing_schema_is_uncertainty(self):
        report = generate_report(request('MATCH (n:Unknown) RETURN n'))
        self.assertEqual(report.status, 'compiled')
        self.assertTrue(any(i.certainty == 'unknown' for s in report.semantic_sections for i in s.insights))

    def test_global_aggregate_has_no_witness_existence_requirement(self):
        query = PREFIX + 'WITH count(*) AS n RETURN sum(n) AS total'
        self.assertEqual(findings(query, 'AGG-ELIGIBILITY-AFTER-REDUCTION'), [])
        self.assertTrue(findings(query, 'AGG-PRIOR-AGGREGATE-VALUE-INFLUENCE'))

    def test_group_key_dependencies_survive_identity_projection(self):
        query = PREFIX + 'WITH b, count(*) AS n RETURN count(*) AS total'
        self.assertTrue(findings(query, 'AGG-DISTINCT-KEY-DEPENDENCY'))

    def test_formula_keeps_distinct_and_input_weight(self):
        report = generate_report(request(PREFIX + 'RETURN avg(DISTINCT b.balance) AS average'))
        self.assertIn('AVG(DISTINCT', report.clauses[-1].formula)
        self.assertIn('weight=', report.clauses[-1].formula)
        self.assertIn('FROM', report.clauses[-1].formula)

    def test_unknown_function_and_volatile_formula_are_explicit(self):
        for expression in ('unmodelled(1)', 'rand()'):
            with self.subTest(expression=expression):
                report = generate_report(request(f'RETURN {expression} AS x'))
                self.assertEqual(report.status, 'compiled')
                self.assertIsNone(report.clauses[-1].formula)
                self.assertTrue(report.clauses[-1].limitation)

    def test_match_after_aggregation_is_not_a_single_group_output(self):
        query = 'MATCH (b:BankAccount) WITH count(*) AS n MATCH (f:FinancialInstitute) RETURN n, f'
        report = generate_report(request(query))
        self.assertIn('MATCH extending', ' '.join(report.semantic_fingerprint))


class BagTests(unittest.TestCase):
    def test_three_trials_preserve_avg_weighting(self):
        self.assertEqual([result(q).rows for q in TRIALS], [((17.5, 4),), ((17.5, 3),), ((20.0, 3),)])

    def test_distinct_identity_is_not_property_equality(self):
        self.assertEqual(result(PREFIX + 'RETURN count(DISTINCT b), count(DISTINCT b.balance)').rows, ((3, 2),))

    def test_plain_with_where_is_applied(self):
        self.assertEqual(result('MATCH (b:BankAccount) WITH b WHERE b.balance > 20 RETURN b.balance').rows, ((40,),))

    def test_plain_return_order_is_applied_before_limit(self):
        self.assertEqual(result('MATCH (b:BankAccount) RETURN b.balance AS balance ORDER BY balance DESC LIMIT 1').rows, ((40,),))

    def test_modifiers_occur_once(self):
        report = generate_report(request('MATCH (b:BankAccount) WITH DISTINCT b, count(*) AS n ORDER BY n WHERE n > 0 RETURN b'))
        self.assertEqual(report.clauses[1].operations, ('AggregateBag', 'DistinctBag', 'SortBag', 'FilterBag'))

    def test_order_by_repeated_group_or_aggregate_expression(self):
        by_key = 'MATCH (b:BankAccount) RETURN b.balance AS balance, count(*) AS n GROUP BY b.balance ORDER BY b.balance DESC'
        self.assertEqual(result(by_key).rows, ((40, 1), (10, 2)))
        by_count = 'MATCH (b:BankAccount) RETURN b.balance AS balance, count(*) AS n ORDER BY count(*) DESC'
        self.assertEqual(result(by_count).rows, ((10, 2), (40, 1)))

    def test_empty_aggregates(self):
        self.assertEqual(result('MATCH (b:Missing) RETURN count(*), sum(b.balance), avg(b.balance), collect(b)').rows, ((0, 0, None, []),))
        self.assertEqual(result('MATCH (b:Missing) RETURN b, count(*)').rows, ())

    def test_scope_drops_and_reintroduction_is_fresh(self):
        report = generate_report(request('MATCH (b) WITH b.balance AS balance RETURN b'))
        self.assertEqual(report.status, 'compilation_failed')
        states = compile_request(request('MATCH (b:BankAccount) WITH b.balance AS balance MATCH (b:BankAccount) RETURN b'))
        self.assertNotEqual(states[1].scope[0].symbol, states[3].scope[1].symbol)

    def test_null_and_boolean_are_distinct_from_zero(self):
        self.assertEqual(result('WITH $x AS x WHERE x IS NULL RETURN x', parameters={'x': None}).rows, ((None,),))
        self.assertEqual(result('WITH $x AS x WHERE x = 0 RETURN x', parameters={'x': False}).rows, ())

    def test_filter_follows_with_pagination(self):
        self.assertEqual(result('MATCH (b:BankAccount) WITH b ORDER BY b.balance LIMIT 1 WHERE b.balance > 20 RETURN b').rows, ())
        self.assertEqual(result('MATCH (b:BankAccount) WITH b.balance AS v ORDER BY v LIMIT 1 WHERE b.balance > 20 RETURN v').rows, ())

    def test_integer_division_and_remainder(self):
        for a, b, div, rem in [(5, 2, 2, 1), (-5, 2, -2, -1), (5, -2, -2, 1), (-5, -2, 2, -1),
                               (9007199254740993, 1, 9007199254740993, 0)]:
            with self.subTest(a=a, b=b):
                self.assertEqual(result('RETURN $a / $b, $a % $b', parameters={'a': a, 'b': b}).rows, ((div, rem),))
        self.assertEqual(result('RETURN 5 / 2 AS x').types, ('INTEGER',))

    def test_parameter_runtime_types_do_not_use_python_coercions(self):
        for query, params in [('RETURN head($x)', {'x': 'abc'}), ('RETURN size($x)', {'x': {'a': 1}}),
                              ('RETURN abs($x)', {'x': True}), ('RETURN $x * 2', {'x': 'abc'}),
                              ('RETURN sum($x)', {'x': True})]:
            with self.subTest(query=query), self.assertRaises(EvaluationError):
                result(query, parameters=params)

    def test_null_property_lookup(self):
        self.assertEqual(result('WITH null AS n RETURN n.value').rows, ((None,),))

    def test_coalesce_short_circuits_and_preserves_node_type(self):
        self.assertEqual(result('RETURN coalesce(1, 1 / 0)').rows, ((1,),))
        query = PREFIX + 'RETURN count(DISTINCT coalesce(b, b)) AS total'
        self.assertIn('node identities', findings(query, 'AGG-DISTINCT-ARGUMENT')[0].explanation)

    def test_nested_null_equality_is_not_bag_equality(self):
        for value in ([None], {'x': None}):
            with self.subTest(value=value):
                self.assertEqual(result('WITH $x AS x WHERE x = $x RETURN x', parameters={'x': value}).rows, ())
                self.assertEqual(result('WITH $x AS x RETURN DISTINCT x', parameters={'x': value}).count, 1)

    def test_invalid_aggregate_forms_are_rejected(self):
        for query in ('RETURN count()', 'RETURN count(DISTINCT *)'):
            with self.subTest(query=query):
                try:
                    report = generate_report(request(query))
                except (ValueError, CypherastError):
                    continue  # Either the parser or compiler may reject invalid syntax.
                self.assertEqual(report.status, 'compilation_failed')

    def test_relationship_uniqueness_is_clause_local(self):
        same = 'MATCH (b:BankAccount)-[r:FROM]->(f), (b)-[r:FROM]->(f) RETURN b'
        separate = 'MATCH (b:BankAccount)-[r:FROM]->(f) MATCH (b)-[r:FROM]->(f) RETURN b'
        self.assertEqual(result(same).count, 0)
        self.assertEqual(result(separate).count, 4)

    def test_undirected_self_loop_is_not_doubled(self):
        graph = PropertyGraph((NodeRecord('a', frozenset({'BankAccount'})),),
                              (RelationshipRecord('loop', 'a', 'a', 'R'),))
        self.assertEqual(result('MATCH (a)-[r:R]-(b) RETURN r', graph).count, 1)

    def test_parallel_edge_multiplicity_composes_for_many_graphs(self):
        # Independently known cardinalities, including empty input and parallel edges.
        for incoming in range(4):
            for outgoing in range(4):
                graph = PropertyGraph(tuple(NodeRecord(n, frozenset({n.upper()})) for n in 'abc'),
                    tuple(RelationshipRecord(f'r{i}', 'a', 'b', 'R') for i in range(incoming)) +
                    tuple(RelationshipRecord(f's{i}', 'b', 'c', 'S') for i in range(outgoing)))
                for projection, expected in [('WITH b', incoming * outgoing),
                                              ('WITH DISTINCT b', int(incoming > 0) * outgoing)]:
                    query = f'MATCH (a:A)-[:R]->(b:B) {projection} MATCH (b)-[:S]->(c:C) RETURN c'
                    with self.subTest(incoming=incoming, outgoing=outgoing, projection=projection):
                        self.assertEqual(result(query, graph).count, expected)

    def test_ordered_projection_and_alias_shadowing_evaluate_once(self):
        trace = evaluate_request(request('MATCH (b:BankAccount) WITH rand() AS x ORDER BY b.balance RETURN x'), fixture())
        self.assertEqual(trace[-1].rows, trace[-2].rows)
        self.assertEqual(result('WITH 1 AS n WITH n + 1 AS n WHERE n > 1 RETURN n').rows, ((2,),))


if __name__ == '__main__':
    unittest.main()
