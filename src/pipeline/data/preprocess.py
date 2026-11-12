"""Translate each dataset's rows into question, schema and Cypher records."""
from copy import deepcopy
import json

from pipeline.data.neo4j_schema import normalize_schema


def preprocess_mtq(row):
    row = dict(row)
    schema = row['schema']
    schema = json.loads(schema) if isinstance(schema, str) else deepcopy(schema)
    for section in ('node_props', 'rel_props'):
        for properties in schema.get(section, {}).values():
            for prop in properties:
                if 'type' not in prop and 'datatype' in prop:
                    prop['type'] = prop.pop('datatype')
    row['schema'] = schema
    return row


def preprocess_cypherbench(row, schema):
    return {**row, 'question': row['nl_question'], 'cypher': row['gold_cypher'],
            'schema': normalize_schema(schema), 'expected_result': row.get('answer_json')}
