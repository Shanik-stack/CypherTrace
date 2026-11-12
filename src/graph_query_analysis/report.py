"""Explain the evolving symbolic result of a Cypher query, operator by operator.

No query execution and no query-specific mistake catalogue. Each explanation
comes from the compiler's actual operator chain, in execution order. Full
multiplicity expressions remain available in algebraic_translation.py;
this report uses short local equations that refer to preceding states.
"""

from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from graph_query_analysis.algebraic_translation import (
    SymbolicSemanticsUnsupported, format_predicate, format_value, symbolic_bag,
)
from graph_query_analysis.compiler import compile_request_progress
from graph_query_analysis.model import NodeSymbol, RelationshipSymbol
from graph_query_analysis.expressions import (
    AggregateBag, DistinctBag, ExtendBag, FilterBag, MatchBag, ProjectBag,
    SliceBag, SortBag, SymbolReference, PropertyAccess,
)


def _new_operators(old, new):
    """Return only the operators added by a clause, in application order."""
    operations = []
    while new is not old:
        operations.append(new)
        new = new.source
    return list(reversed(operations))


def _pattern(op):
    paths = []
    attached = set()
    for rel in op.relationships:
        left = rel.left.name.replace('\x00', '@')
        right = rel.right.name.replace('\x00', '@')
        attached.update((rel.left, rel.right))
        types = ':' + '|'.join(rel.types) if rel.types else ''
        if rel.direction == 'INCOMING':
            paths.append(f'({left})<-[{types}]-({right})')
        elif rel.direction == 'BOTH':
            paths.append(f'({left})-[{types}]-({right})')
        else:
            paths.append(f'({left})-[{types}]->({right})')
    for node in op.nodes:
        if node.symbol not in attached:
            labels = ':' + ':'.join(node.labels) if node.labels else ''
            paths.append(f'({node.symbol.name.replace(chr(0), "@")}{labels})')
    conditions = []
    for node in op.nodes:
        if node.labels and node.symbol in attached:
            conditions.append(f'{node.symbol.name.replace(chr(0), "@")}:{":".join(node.labels)}')
        conditions.extend(format_predicate(p).replace('\x00', '@') for p in node.predicates)
    for rel in op.relationships:
        conditions.extend(format_predicate(p).replace('\x00', '@') for p in rel.predicates)
    pattern = ', '.join(paths) or '(empty graph pattern)'
    return pattern + (f'; constraints: {" AND ".join(conditions)}' if conditions else '')


def _columns(columns):
    return ', '.join(
        f'{column.alias or column.symbol.name.replace(chr(0), "@")} = '
        f'{format_value(column.expression).replace(chr(0), "@")}'
        for column in columns
    )


def _operation(op):
    """Local mathematical rule and its query-specific inputs, not an error label."""
    if isinstance(op, MatchBag):
        return ('MATCH',
                f'For each incoming row, enumerate compatible assignments to {_pattern(op)}. '
                'Every matching relationship identity is a separate witness; rows may multiply.',
                'μ_out(x,w) = μ_in(x) × [pattern(x,w)]')
    if isinstance(op, FilterBag):
        condition = format_predicate(op.predicate).replace('\x00', '@')
        return ('WHERE', f'Keep rows only if {condition} is TRUE (FALSE and NULL are removed).',
                f'μ_out(x) = μ_in(x) × [{condition} is TRUE]')
    if isinstance(op, ExtendBag):
        return ('EXTEND', f'Compute {_columns(op.columns)} without removing incoming bindings.',
                'μ_out(x,y) = μ_in(x) × [y = expression(x)]')
    if isinstance(op, ProjectBag):
        return ('PROJECT', f'Output {_columns(op.columns)}. Equal projected rows retain summed multiplicities.',
                'μ_out(y) = Σ_{x: project(x)=y} μ_in(x)')
    if isinstance(op, DistinctBag):
        return ('DISTINCT', 'Keep one occurrence of each distinct output tuple (values or entity identities).',
                'μ_out(y) = 1 if μ_in(y) > 0, otherwise 0')
    if isinstance(op, AggregateBag):
        keys = ', '.join(format_value(k.expression).replace('\x00', '@') for k in op.grouping_keys)
        aggregates = ', '.join(format_value(a.expression).replace('\x00', '@')
                               for a in op.aggregate_computations)
        scope = f'group by {keys}' if keys else 'one global group, including an empty-input group'
        formulas = []
        for aggregation in op.aggregate_computations:
            call = aggregation.expression
            value = format_value(call.argument) if call.argument is not None else '*'
            if call.distinct:
                formulas.append(f'{call.name.upper()}(DISTINCT {value}): deduplicate non-NULL argument values within each group')
            elif call.name == 'count':
                formulas.append('COUNT(*) = Σ μ_in(x)' if call.argument is None else
                                f'COUNT({value}) = Σ μ_in(x) × [{value} is not NULL]')
            elif call.name == 'avg':
                formulas.append(f'AVG({value}) = Σ μ_in(x)·{value}(x) / Σ μ_in(x), over non-NULL {value}; NULL if denominator is 0')
            elif call.name == 'sum':
                formulas.append(f'SUM({value}) = Σ μ_in(x)·{value}(x), over non-NULL {value}; NULL if none')
            else:
                formulas.append(f'{call.name.upper()}({value}): aggregate over non-NULL values within each group')
        outputs = ', '.join(c.alias or format_value(c.expression) for c in op.columns)
        return ('AGGREGATE',
                f'Form {scope}; compute {aggregates or "group values"}; output {outputs}. '
                'Each surviving input occurrence contributes its multiplicity. '
                'COUNT(*) includes all rows; other aggregates ignore NULL arguments. '
                'DISTINCT aggregate arguments deduplicate values, not entity identities.',
                '; '.join(formulas) if formulas else 'One row per present group; one global row even on empty input')
    if isinstance(op, SortBag):
        keys = ', '.join(format_value(k.expression).replace('\x00', '@') +
                         (' DESC' if k.descending else ' ASC') for k in op.keys)
        return ('ORDER', f'Order by {keys}; row counts are unchanged. Ties have unspecified order.',
                'μ_out = μ_in (ordering metadata changes)')
    if isinstance(op, SliceBag):
        bounds = ', '.join(f'{name} {format_value(value)}' for name, value in
                           (('SKIP', op.skip), ('LIMIT', op.limit)) if value is not None)
        return ('SLICE', f'{bounds}: select row occurrences from a stream. '
                'Which rows survive cannot be derived from an unordered bag.',
                'No exact μ_out without ordered-stream semantics')
    raise NotImplementedError(f'No report for {type(op).__name__}')


def _property_reads(value):
    """Find actual property accesses in one operator, not its earlier source tree."""
    if isinstance(value, PropertyAccess):
        yield value
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from _property_reads(item)
    elif is_dataclass(value):
        for field in fields(value):
            if field.name != 'source':
                yield from _property_reads(getattr(value, field.name))


def _schema_checks(op, schema, node_labels, relationship_types, aliases):
    """Compare requirements and property reads with supplied schema evidence.

    An observed schema is an inventory, not a constraint. 'Not listed' never
    means the property or relationship is impossible in the real graph.
    """
    notes = []
    known_labels = set(schema.node_properties)
    known_labels.update(r.start_label for r in schema.relationships)
    known_labels.update(r.end_label for r in schema.relationships)

    if isinstance(op, MatchBag):
        for node in op.nodes:
            node_labels.setdefault(node.symbol, set()).update(node.labels)
            for label in node.labels:
                if label not in known_labels:
                    notes.append(f'Node label {label}: not listed in schema')

        for rel in op.relationships:
            relationship_types.setdefault(rel.symbol, set()).update(rel.types)
            left = node_labels.get(rel.left, set())
            right = node_labels.get(rel.right, set())
            for rel_type in rel.types:
                signatures = schema.signatures_for(rel_type)
                if not signatures:
                    notes.append(f'Relationship {rel_type}: not listed in schema')
                elif left and right:
                    compatible = any(
                        (rel.direction in ('OUTGOING', 'BOTH') and
                         sig.start_label in left and sig.end_label in right) or
                        (rel.direction in ('INCOMING', 'BOTH') and
                         sig.start_label in right and sig.end_label in left)
                        for sig in signatures
                    )
                    if compatible:
                        notes.append(f'{rel_type} ({"/".join(sorted(left))} -> '
                                     f'{"/".join(sorted(right))}, {rel.direction}): listed signature compatible')
                    else:
                        notes.append(f'{rel_type} ({"/".join(sorted(left))} -> '
                                     f'{"/".join(sorted(right))}, {rel.direction}): '
                                     'no compatible signature listed')
                else:
                    notes.append(f'Relationship {rel_type}: type listed; endpoint labels not fully known')

    for prop in _property_reads(op):
        if not isinstance(prop.owner, SymbolReference):
            continue
        symbol = prop.owner.symbol
        if isinstance(symbol, NodeSymbol):
            labels = node_labels.get(symbol, set())
            if labels:
                evidence = [(label, schema.node_property_type(label, prop.property_name))
                            for label in sorted(labels)]
                listed = [f'{label} ({kind})' for label, kind in evidence if kind is not None]
                if listed:
                    notes.append(f'{aliases.get(symbol, symbol.name.replace(chr(0), "@"))}.'
                                 f'{prop.property_name}: listed on {", ".join(listed)}')
                else:
                    notes.append(f'{aliases.get(symbol, symbol.name.replace(chr(0), "@"))}.'
                                 f'{prop.property_name}: not listed for {", ".join(sorted(labels))}')
            else:
                notes.append(f'{aliases.get(symbol, symbol.name.replace(chr(0), "@"))}.'
                             f'{prop.property_name}: unknown (node labels unavailable)')
        elif isinstance(symbol, RelationshipSymbol):
            types = relationship_types.get(symbol, set())
            if types:
                listed = [f'{name} ({schema.relationship_property_type(name, prop.property_name)})'
                          for name in sorted(types)
                          if schema.relationship_property_type(name, prop.property_name) is not None]
                notes.append(f'{aliases.get(symbol, symbol.name.replace(chr(0), "@"))}.'
                             f'{prop.property_name}: '
                             + (f'listed for {", ".join(listed)}' if listed else
                                f'not listed for relationship types {", ".join(sorted(types))}'))
            else:
                notes.append(f'{aliases.get(symbol, symbol.name.replace(chr(0), "@"))}.'
                             f'{prop.property_name}: unknown (relationship type unavailable)')

    return tuple(dict.fromkeys(notes))


@dataclass
class QueryReport:
    query_text: str
    dialect: str
    schema_completeness: str
    status: str
    steps: tuple[dict, ...]
    compilation_error: str | None = None
    limitations: tuple[str, ...] = ()

    @property
    def symbolic_status(self):
        return 'partial' if self.limitations or self.compilation_error else 'supported'

    @property
    def model_report(self):
        """Use this entire compact trace for the LLM, without character clipping."""
        return self.render()

    def render(self, *, show_formulas=True, max_formula_chars=None):
        lines = [f'Compilation: {self.status}; mathematical interpretation: {self.symbolic_status}.',
                 f'Schema: {self.schema_completeness} (observations are not uniqueness or existence guarantees).',
                 'S0: one empty binding with multiplicity 1; graph records are not accessed.']
        for step in self.steps:
            lines.append(f"{step['output']} ← {step['input']} | clause {step['clause']} | {step['operator']}")
            lines.append(f"  {step['effect']}")
            if show_formulas:
                lines.append(f"  {step['rule']}")
            if step.get('schema_checks'):
                lines.append('  Schema evidence: ' + '; '.join(step['schema_checks']))
            if step.get('row_slots') is not None:
                lines.append(f"  Row binding slots: {', '.join(step['row_slots']) or '(one empty tuple)'}")
            if step.get('scope') is not None:
                lines.append(f"  Variables visible after clause: {', '.join(step['scope']) or '(none)'}")
                if step.get('discarded'):
                    lines.append(f"  Names leaving scope: {', '.join(step['discarded'])}")
            if step.get('limitation'):
                lines.append(f"  Mathematics incomplete: {step['limitation']}")
        if any('not listed' in note or 'no compatible signature' in note
               for step in self.steps for note in step.get('schema_checks', ())):
            lines.append('Schema caution: not listed means missing supplied evidence; '
                         'an observed schema does not prove impossibility.')
        if self.compilation_error:
            lines.append(f'Compilation stopped: {self.compilation_error}')
            lines.append('Unsupported by this analyzer does not necessarily mean invalid Neo4j Cypher.')
        lines.append('Interpretation only: no intent correctness or concrete rows established. '
                     'A possible multiplicity effect does not by itself establish an error.')
        return '\n'.join(lines)


def generate_report(request, states=None):
    """Use compiler states and all operators within each successfully compiled clause."""
    error = None
    if states is None:
        states, error = compile_request_progress(request)

    steps = []
    limitations = []
    cache = {}
    node_labels = {}
    relationship_types = {}
    for clause_index in range(1, len(states)):
        before, after = states[clause_index-1], states[clause_index]
        ops = _new_operators(before.bag, after.bag)
        for op_index, op in enumerate(ops):
            name, effect, rule = _operation(op)
            stage = len(steps) + 1
            rule = rule.replace('μ_out', f'μ_{stage}').replace('μ_in', f'μ_{stage-1}')
            step = {
                'number': stage, 'clause': clause_index, 'operator': name,
                'input': f'S{stage-1}', 'output': f'S{stage}',
                'effect': effect, 'rule': rule,
                'schema_checks': _schema_checks(op, request.schema, node_labels, relationship_types,
                                                 {binding.symbol: binding.variable for binding in before.scope}),
            }
            # Mathematical support is checked on the *actual current* operator
            # tree, never on a rewritten or independently reconstructed query.
            try:
                symbolic = symbolic_bag(op, cache)
                aliases = {c.symbol: c.alias for c in getattr(op, 'columns', ()) if c.alias}
                step['row_slots'] = tuple(aliases.get(sym) or sym.name.replace('\x00', '@')
                                          for sym in symbolic.symbols)
                input_symbols = symbolic_bag(op.source, cache).symbols
                dropped_slots = tuple(sym.name.replace('\x00', '@') for sym in input_symbols
                                      if sym not in symbolic.symbols)
                introduced_slots = tuple(sym.name.replace('\x00', '@') for sym in symbolic.symbols
                                         if sym not in input_symbols)
                if isinstance(op, ProjectBag) and dropped_slots:
                    step['effect'] += f' Collapsed row slots: {", ".join(dropped_slots)}.'
                if isinstance(op, MatchBag) and introduced_slots:
                    step['effect'] += f' New row slots: {", ".join(introduced_slots)}.'
                if isinstance(op, AggregateBag) and dropped_slots:
                    step['effect'] += f' Group aggregation consumes row slots: {", ".join(dropped_slots)}.'
            except SymbolicSemanticsUnsupported as exc:
                step['limitation'] = str(exc)
                limitations.append(f"S{stage}: {exc}")
            if op_index == len(ops) - 1:
                step['scope'] = tuple(binding.variable for binding in after.scope)
                removed = tuple(binding.variable for binding in before.scope
                                if binding.variable not in step['scope'])
                if removed:
                    step['discarded'] = removed
            steps.append(step)

    message = str(error) if error is not None else None
    return QueryReport(request.query_text, request.dialect, request.schema.completeness,
                       'compilation_failed' if error else 'compiled', tuple(steps),
                       message, tuple(limitations))


def get_reports(requests_path, show_formulas=True, output_path=None):
    from graph_query_analysis.input import load_requests
    text = '\n\n'.join(generate_report(r).render(show_formulas=show_formulas)
                       for r in load_requests(requests_path))
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return text
