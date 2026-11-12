"""Optional read-only conformance probes. No graph data is read or written."""
import os
import unittest


@unittest.skipUnless(os.getenv('CYPHER_TEST_NEO4J_URI'), 'No live Neo4j test endpoint configured')
class Neo4jTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from neo4j import GraphDatabase
        cls.driver = GraphDatabase.driver(os.environ['CYPHER_TEST_NEO4J_URI'],
            auth=(os.getenv('CYPHER_TEST_NEO4J_USER', 'neo4j'), os.environ['CYPHER_TEST_NEO4J_PASSWORD']))

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def test_integer_arithmetic(self):
        with self.driver.session(default_access_mode='READ') as session:
            rows = session.run('RETURN 5 / 2 AS q, -5 / 2 AS n, -5 % 2 AS r').values()
            self.assertEqual(rows, [[2, -2, -1]])

    def test_where_after_pagination(self):
        with self.driver.session(default_access_mode='READ') as session:
            rows = session.run('UNWIND [1, 2, 3] AS x WITH x ORDER BY x LIMIT 1 WHERE x > 1 RETURN x').values()
            self.assertEqual(rows, [])
