from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pipeline.data.load_data import load_records
from pipeline.data.neo4j_schema import normalize_schema, normalize_type
from test_python_semantics import SCHEMA


class SchemaTests(unittest.TestCase):
    def test_normalization_is_independent_and_preserves_metadata(self):
        raw = {**deepcopy(SCHEMA), 'assumptions': [{'kind': 'observed'}],
               'metadata': {'constraint': ['explicit fact']}}
        raw['relationships'][0]['properties'] = [{'property': 'since', 'type': 'INTEGER'}]
        before = deepcopy(raw)
        normalized = normalize_schema(raw)
        self.assertEqual(raw, before)
        self.assertEqual(normalized['assumptions'], raw['assumptions'])
        self.assertEqual(normalized['metadata'], raw['metadata'])
        self.assertEqual(normalized['relationships'], raw['relationships'])
        normalized['node_props']['BankAccount'][0]['type'] = 'STRING'
        self.assertEqual(raw, before)

    def test_type_aliases_and_conflicts(self):
        self.assertEqual(normalize_type('list[list[int]]'), 'LIST<LIST<INTEGER>>')
        schema = deepcopy(SCHEMA)
        schema['node_props']['BankAccount'].append({'property': 'balance', 'type': 'STRING'})
        with self.assertRaises(ValueError):
            normalize_schema(schema)

    def test_same_relationship_type_keeps_multiple_endpoints_and_properties(self):
        raw = {'entities': [{'label': n, 'properties': {}} for n in ('A', 'B', 'C')],
               'relations': [
                   {'subj_label': 'A', 'label': 'R', 'obj_label': 'B', 'properties': {'value': 'int'}},
                   {'subj_label': 'C', 'label': 'R', 'obj_label': 'B', 'properties': {'value': 'string'}},
               ]}
        result = normalize_schema(raw)
        self.assertEqual(len(result['relationships']), 2)
        self.assertEqual([r['properties'][0]['type'] for r in result['relationships']], ['INTEGER', 'STRING'])
        self.assertEqual(result['rel_props']['R'][0]['type'], 'INTEGER | STRING')

    def test_duplicate_signatures_and_idempotence(self):
        schema = deepcopy(SCHEMA)
        schema['relationships'] *= 2
        once = normalize_schema(json.dumps(schema))
        self.assertEqual(len(once['relationships']), 1)
        self.assertEqual(once, normalize_schema(once))

    def test_dataset_loads_only_selected_schemas(self):
        # This uses a fake dataset module, so the offline suite needs no datasets extra.
        rows = [{'question': 'unused', 'schema': 'invalid'},
                {'question': 'selected', 'schema': json.dumps(SCHEMA), 'cypher': 'RETURN 1'}]
        loader = Mock(return_value=rows)
        with patch.dict('sys.modules', {'datasets': SimpleNamespace(load_dataset=loader)}):
            result = load_records(indices=(1, 1))
        self.assertEqual([r['question'] for r in result], ['selected', 'selected'])
        self.assertIsNot(result[0]['schema'], result[1]['schema'])
        loader.assert_called_once()

    def test_dataset_limit_selects_first_rows_or_whole_split(self):
        rows = [{'question': str(i), 'schema': SCHEMA, 'cypher': 'RETURN 1'} for i in range(3)]
        loader = Mock(return_value=rows)
        with patch.dict('sys.modules', {'datasets': SimpleNamespace(load_dataset=loader)}):
            for limit, expected in ((2, ['0', '1']), ('total', ['0', '1', '2']), (5, ['0', '1', '2'])):
                with self.subTest(limit=limit):
                    selected = load_records(split='test', limit=limit)
                    self.assertEqual([record['question'] for record in selected], expected)
        with self.assertRaisesRegex(ValueError, 'positive integer'):
            load_records(limit=0)
        with self.assertRaisesRegex(ValueError, 'either indices or limit'):
            load_records(indices=(0,), limit='total')

    def test_mtq_preprocessing_preserves_supplied_schema(self):
        schema = deepcopy(SCHEMA)
        schema['relationships'].append(deepcopy(schema['relationships'][0]))
        rows = [{'question': 'q', 'schema': schema, 'cypher': 'RETURN 1'}]
        with patch.dict('sys.modules', {'datasets': SimpleNamespace(load_dataset=Mock(return_value=rows))}):
            selected = load_records(indices=(0,))
        self.assertEqual(selected[0]['schema'], schema)
        self.assertIsNot(selected[0]['schema'], schema)
        self.assertEqual(len(selected[0]['schema']['relationships']), 2)

    def test_mtq_datatype_field_is_converted_to_type(self):
        schema = deepcopy(SCHEMA)
        balance = schema['node_props']['BankAccount'][0]
        balance['datatype'] = balance.pop('type')
        rows = [{'question': 'q', 'schema': schema, 'cypher': 'RETURN 1'}]
        with patch.dict('sys.modules', {'datasets': SimpleNamespace(load_dataset=Mock(return_value=rows))}):
            selected = load_records(indices=(0,))
        self.assertEqual(selected[0]['schema'], SCHEMA)
        self.assertIn('datatype', balance)

    def test_cypherbench_preprocessing_converts_only_selected_graph(self):
        schema = {'entities': [{'label': name, 'properties': {}}
                               for name in ('Account', 'Bank')],
                  'relations': [{'subj_label': 'Account', 'obj_label': 'Bank',
                                 'label': 'FROM', 'properties': {'since': 'int'}}]}
        rows = [{'graph': 'unused'},
                {'graph': 'selected', 'nl_question': 'Which bank?',
                 'gold_cypher': 'MATCH (b:Bank) RETURN b', 'answer_json': '[]'}]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'schema.json'
            path.write_text(json.dumps(schema), encoding='utf-8')
            download = Mock(return_value=str(path))
            modules = {'datasets': SimpleNamespace(load_dataset=Mock(return_value=rows)),
                       'huggingface_hub': SimpleNamespace(hf_hub_download=download)}
            with patch.dict('sys.modules', modules):
                selected = load_records('cypherbench', split='test', indices=(1, 1))
        download.assert_called_once()
        self.assertEqual(selected[0]['question'], 'Which bank?')
        self.assertEqual(selected[0]['schema']['relationships'][0]['properties'][0]['type'], 'INTEGER')
        self.assertEqual(selected[0]['expected_result'], '[]')
        self.assertIsNot(selected[0]['schema'], selected[1]['schema'])


if __name__ == '__main__':
    unittest.main()
