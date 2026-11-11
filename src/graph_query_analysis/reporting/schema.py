"""Observed/exhaustive schema comparisons without assuming instance facts."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section, property_accesses
from graph_query_analysis.expressions import MatchBag, FilterBag, ProjectBag, ExtendBag, AggregateBag, SortBag, SymbolReference


def build(ctx: AnalysisContext) -> Section:
    schema = ctx.request.schema
    details: list[str] = [f'Schema completeness: {schema.completeness}.']
    insights: list[Insight] = []
    for e in ctx.matches:
        for r in e.operator.relationships:
            if not r.types:
                details.append(f'Clause {e.clause}: relationship {ctx.symbol(r.symbol, e.clause)} '
                               'has no type restriction; schema signatures alone cannot '
                               'restrict its actual identity.')
                continue
            left_labels = set(ctx.labels_at(r.left, e.clause))
            right_labels = set(ctx.labels_at(r.right, e.clause))
            for rel_type in r.types:
                candidates = schema.signatures_for(rel_type)
                compatible = []
                for sign in candidates:
                    orientations = ((sign.start_label, sign.end_label),)
                    if r.direction in ('BOTH', 'INCOMING'):
                        orientations = ((sign.end_label, sign.start_label),)
                        if r.direction == 'BOTH':
                            orientations += ((sign.start_label, sign.end_label),)
                    if any((not left_labels or a in left_labels) and
                           (not right_labels or b in right_labels) for a, b in orientations):
                        compatible.append(sign)
                requested = (f'{ctx.symbol(r.left, e.clause)} -[{rel_type}, {r.direction}]- '
                             f'{ctx.symbol(r.right, e.clause)}')
                if compatible:
                    details.append(f'Clause {e.clause}: {requested}; '
                                   f'{len(compatible)} supplied signature(s) are compatible '
                                   'with required labels and direction.')
                else:
                    details.append(f'Clause {e.clause}: {requested}; no supplied relationship '
                                   'signature confirms this specific pattern.')
                    insights.append(Insight('SCHEMA-SIGNATURE-UNCONFIRMED',
                        'Required relationship signature is not confirmed by supplied schema',
                        f'No supplied {rel_type} signature matches the required endpoint '
                        f'label/direction restrictions for {requested}. '
                        + ('This observed-schema gap does not establish impossibility.'
                           if schema.completeness == 'observed' else
                           'The caller declares its schema exhaustive, so this discrepancy '
                           'requires review against the exact completeness contract.'),
                        'unknown' if schema.completeness == 'observed' else 'possible',
                        'high', e.clause, (requested,),
                        'Does the requested traversal match the intended schema relationship?'))
        for node in e.operator.nodes:
            for label in node.labels:
                if label not in schema.node_properties and not any(
                    label in (s.start_label, s.end_label) for s in schema.relationships):
                    details.append(f'Clause {e.clause}: node label :{label} is not in '
                                   'supplied node properties or relationship signatures.')
                    insights.append(Insight('SCHEMA-LABEL-UNCONFIRMED',
                        'Node label is not confirmed by the schema inventory',
                        f'Label :{label} is absent from the supplied schema inventory. '
                        'This is not proof that no such nodes exist when the schema is observed.',
                        'unknown', 'medium', e.clause, (label,)))
    # Check property references against the inventory, without interpreting an
    # absent observed property as a semantic error.
    property_reads = []
    for e in ctx.events:
        op = e.operator
        if isinstance(op, MatchBag):
            values = [p for n in op.nodes for p in n.predicates] + [
                p for r in op.relationships for p in r.predicates]
        elif isinstance(op, FilterBag):
            values = [op.predicate]
        elif isinstance(op, (ProjectBag, ExtendBag)):
            values = [c.expression for c in op.columns]
        elif isinstance(op, AggregateBag):
            values = ([g.expression for g in op.grouping_keys] +
                      [a.expression for a in op.aggregate_computations])
        elif isinstance(op, SortBag):
            values = [k.expression for k in op.keys]
        else:
            values = []
        for value in values:
            for access in property_accesses(value):
                if isinstance(access.owner, SymbolReference):
                    property_reads.append((e.clause, access.owner.symbol, access.property_name))
    for clause, sym, prop in dict.fromkeys(property_reads):
        labels = ctx.labels_at(sym, clause)
        rel_types = ctx.types_at(sym, clause)
        if not labels and not rel_types:
            continue
        declared = ([schema.node_property_type(label, prop) for label in labels] +
                    [schema.relationship_property_type(kind, prop) for kind in rel_types])
        declared = [t for t in declared if t is not None]
        expression = f'{ctx.name(sym, clause)}.{prop}'
        if declared:
            details.append(f'Clause {clause} reads {expression}: schema lists property '
                           f'type(s) {", ".join(sorted(set(declared)))}. This is not '
                           'a non-NULL existence guarantee.')
        else:
            details.append(f'Clause {clause} reads {expression}: its property is not '
                           'listed for the known labels/types in the supplied inventory.')
            insights.append(Insight('SCHEMA-PROPERTY-UNCONFIRMED',
                'A referenced property is not confirmed by the supplied schema',
                f'{expression} is absent from known property descriptions for '
                f'{ctx.symbol(sym, clause)}. This is a coverage/naming question, not proof '
                'that the expression is invalid on an observed schema.',
                'unknown', 'medium', clause, (expression,),
                'Is this the intended property name for the entity type?'))
    details.append('A listed property describes schema observations or type declarations; '
                   'it does not imply every matching entity has a non-NULL property. '
                   'This report does not infer uniqueness, cardinality, or existence constraints.')
    return Section('schema', 'Schema-grounded traversal and uncertainty',
        'Compare required path structure with supplied signatures while retaining '
        'the distinction between observed inventory and exhaustive guarantees.',
        tuple(details), tuple(insights))
