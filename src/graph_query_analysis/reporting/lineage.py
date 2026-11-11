"""Result-column value, selection, and stage-aware multiplicity provenance."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section, property_accesses, aggregate_calls
from graph_query_analysis.expressions import AggregateBag, FilterBag


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    columns = ctx.columns()
    if not columns:
        return Section('lineage', 'Output-value and influence lineage',
                       'No final output columns were found.', (), ())

    final = len(ctx.request.clauses)
    filters = [(e.clause, e.operator.predicate) for e in ctx.events
               if isinstance(e.operator, FilterBag)]
    aggregate_events = [e for e in ctx.events if isinstance(e.operator, AggregateBag)]
    final_effects = ctx.influence.final()
    for n, col in enumerate(columns, 1):
        output = col.alias or ctx.expression(col.expression, final)
        origin = ctx.origin_expression(col.expression)
        expression = ctx.expanded_expression(col.expression)
        direct_sources = ctx.resolve(col.expression)
        direct = ', '.join(ctx.symbol(s, final) for s in direct_sources) or '(constant or parameter)'
        details.append(f'Output column {n}: {output} <= {expression}.')
        details.append(f'  Value sources (through aliases): {direct}.')
        props = property_accesses(origin)
        if props:
            details.append('  Direct property reads: ' +
                           ', '.join(ctx.expression(p, final) for p in props) + '.')
        if filters:
            details.append('  Filtering conditions: ' + '; '.join(
                f'clause {clause}: {ctx.predicate(pred, clause)}' for clause, pred in filters) + '.')
        else:
            details.append('  No separate WHERE filter. Mandatory MATCH patterns '
                           'still determine which assignments exist.')
        # Each aggregate has its own input stage. Never infer its witness
        # multiplicity from the final bag or the last query-wide DISTINCT.
        aggs = aggregate_calls(origin)
        relevant_aggregate_events = [e for e in aggregate_events
                                     if any(comp.expression in aggs
                                            for comp in e.operator.aggregate_computations)]
        if aggs and relevant_aggregate_events:
            for e in relevant_aggregate_events:
                facts = ctx.influence.before(e.operator)
                for kind, explanation in (
                    ('occurrences', 'input occurrence counts'),
                    ('distinct_keys', 'number of retained distinct keys'),
                    ('eligibility', 'input eligibility, not raw multiplicity')):
                    witnesses = facts.symbols(kind)
                    if witnesses:
                        details.append(f'  Aggregate clause {e.clause} {explanation}: ' +
                                       ', '.join(ctx.symbol(s, e.clause) for s in witnesses) + '.')
                if not facts.modes:
                    details.append(f'  Aggregate clause {e.clause}: no pattern witness '
                                   'is represented in its symbolic input.')
        elif aggs:
            details.append('  Aggregate lineage is present, but its source operation '
                           'could not be identified uniquely; no witness-specific '
                           'multiplication claim is made.')
        else:
            active = final_effects.symbols('occurrences', 'distinct_keys')
            only_eligibility = final_effects.symbols('eligibility')
            if active:
                details.append('  Potential result occurrence/key dependencies: ' +
                               ', '.join(ctx.symbol(s, final) for s in active) + '.')
            if only_eligibility:
                details.append('  Earlier witness eligibility-only dependencies: ' +
                               ', '.join(ctx.symbol(s, final) for s in only_eligibility) + '.')
        insights.append(Insight('LINEAGE-OUTPUT-COLUMN',
            'Output column has traceable source and stage-specific influences',
            f'{output} is computed from {expression}. Direct value sources: {direct}. '
            'Selection and multiplicity are separately tracked at the relevant '
            'operators; these dependencies do not establish actual database rows.',
            'entailed', 'medium', final,
            evidence=(f'column={output}', f'value={expression}')))

    return Section('lineage', 'Output-value and influence lineage',
        'Trace value sources and independent selection/multiplicity dependencies '
        'at the operator stage that produces each result.',
        tuple(details), tuple(insights))
