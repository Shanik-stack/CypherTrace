"""Convert CypherBench and GraphRAG/Mind-the-Query schemas to one format."""

from copy import deepcopy
import json

ALIASES = {
    'str': 'STRING', 'string': 'STRING', 'categorical': 'STRING',
    'int': 'INTEGER', 'integer': 'INTEGER', 'long': 'INTEGER',
    'float': 'FLOAT', 'double': 'FLOAT', 'bool': 'BOOLEAN',
    'boolean': 'BOOLEAN', 'datetime': 'DATE_TIME',
    'localdatetime': 'LOCAL_DATE_TIME', 'date_time': 'DATE_TIME',
    'local_date_time': 'LOCAL_DATE_TIME', 'local_time': 'LOCAL_TIME',
}


def normalize_type(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Invalid property type: {value!r}')
    text = value.strip()
    if text.lower().startswith('list[') and text.endswith(']'):
        return f'LIST<{normalize_type(text[5:-1])}>'
    if text.lower().startswith('list<') and text.endswith('>'):
        return f'LIST<{normalize_type(text[5:-1])}>'
    return ALIASES.get(text.lower(), text.upper())


def normalize_properties(raw):
    if isinstance(raw, dict):
        entries = ({'property': k, 'type': v} for k, v in raw.items())
    elif isinstance(raw, list):
        entries = raw
    else:
        raise TypeError('Properties must be a dictionary or list')
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError('A property entry must be a dictionary')
        name = entry.get('property')
        if not isinstance(name, str) or not name:
            raise ValueError(f'Invalid property name: {name!r}')
        raw_type = entry.get('type', entry.get('datatype'))
        if 'type' in entry and 'datatype' in entry and (
            normalize_type(entry['type']) != normalize_type(entry['datatype'])
        ):
            raise ValueError(f'Conflicting type/datatype for {name}')
        typ = normalize_type(raw_type)
        item = {k: deepcopy(v) for k, v in entry.items()
                if k not in ('property', 'type', 'datatype')}
        item.update(property=name, type=typ)
        if name in result and result[name]['type'] != typ:
            raise ValueError(f'Conflicting declared types for {name}')
        result.setdefault(name, {}).update(item)
    return [result[n] for n in sorted(result)]


def merge_props(destination, key, props):
    combined = {p['property']: p for p in destination.get(key, [])}
    for p in normalize_properties(props):
        n = p['property']
        if n in combined and combined[n]['type'] != p['type']:
            raise ValueError(f'Conflicting types for {key}.{n}')
        combined.setdefault(n, {}).update(p)
    destination[key] = [combined[n] for n in sorted(combined)]


class Neo4jSchema:
    """Canonical node_props/rel_props/relationships/metadata schema adapter."""

    def __init__(self, schema):
        if isinstance(schema, str):
            schema = json.loads(schema)
        if not isinstance(schema, dict):
            raise TypeError('Schema must be a JSON object or dictionary')
        data = {'node_props': {}, 'rel_props': {}, 'relationships': [],
                'metadata': {'constraint': None, 'index': None}}

        if 'entities' in schema and 'relations' in schema:
            for entity in schema['entities']:
                merge_props(data['node_props'], entity['label'], entity['properties'])
            for relation in schema['relations']:
                data['relationships'].append({
                    'start': relation['subj_label'],
                    'type': relation['label'],
                    'end': relation['obj_label'],
                })
                merge_props(data['rel_props'], relation['label'], relation['properties'])
            # CypherBench should declare all endpoint labels explicitly.
            for r in data['relationships']:
                if r['start'] not in data['node_props'] or r['end'] not in data['node_props']:
                    raise ValueError(f'Undeclared CypherBench endpoint label: {r}')
        elif all(k in schema for k in ('node_props', 'rel_props', 'relationships')):
            for label, props in schema['node_props'].items():
                merge_props(data['node_props'], label, props)
            for typ, props in schema['rel_props'].items():
                merge_props(data['rel_props'], typ, props)
            data['relationships'] = deepcopy(schema['relationships'])
            if schema.get('metadata') is not None:
                data['metadata'] = deepcopy(schema['metadata'])
        else:
            raise ValueError('Unknown schema format')

        signatures = set()
        for r in data['relationships']:
            triple = tuple(r.get(k) for k in ('start', 'type', 'end'))
            if any(not isinstance(x, str) or not x for x in triple):
                raise ValueError(f'Malformed relationship: {r!r}')
            signatures.add(triple)
            data['node_props'].setdefault(triple[0], [])
            data['node_props'].setdefault(triple[2], [])
            data['rel_props'].setdefault(triple[1], [])
        data['relationships'] = [
            {'start': s, 'type': t, 'end': e} for s, t, e in sorted(signatures)
        ]
        data['node_props'] = dict(sorted(data['node_props'].items()))
        data['rel_props'] = dict(sorted(data['rel_props'].items()))
        self._data = data

    def to_dict(self):
        return deepcopy(self._data)
