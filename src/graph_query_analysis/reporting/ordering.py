"""Ranking units, sort stages, limits, and tie/order uncertainty."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section
from graph_query_analysis.expressions import AggregateBag, DistinctBag, ProjectBag, SliceBag, SortBag


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    events = ctx.events
    for i, e in enumerate(events):
        op = e.operator
        if isinstance(op, SortBag):
            keys = tuple(ctx.expression(k.expression) + (' DESC' if k.descending else ' ASC')
                         for k in op.keys)
            group = any(isinstance(x.operator, AggregateBag) for x in events[:i])
            details.append(f'Clause {e.clause}: ORDER BY ' + ', '.join(keys) + '.')
            details.append('  Ranking unit: ' + ('current projected/aggregate-group rows' if group
                        else 'current matching/projected row occurrences') + '.')
            details.append('  ORDER BY reorders rows but does not reduce duplicates or '
                           'change their multiplicities; ties have no guaranteed stable order.')
            details.append('  Sort dependency: ' +
                           ', '.join(ctx.describe_dependencies(k.expression) for k in op.keys) + '.')
        if isinstance(op, SliceBag):
            bound = 'SKIP ' + (ctx.expression(op.skip) if op.skip else '0') + ', LIMIT ' + (
                ctx.expression(op.limit) if op.limit else 'unspecified')
            nearest_sort = next((prev for prev in reversed(events[:i])
                                 if isinstance(prev.operator, SortBag)), None)
            intervening = events[events.index(nearest_sort)+1:i] if nearest_sort else ()
            has_reorder_risk = any(isinstance(z.operator, (AggregateBag, DistinctBag)) for z in intervening)
            details.append(f'Clause {e.clause}: {bound}.')
            details.append('  LIMIT/SKIP selects occurrences at this pipeline position, '
                           'not automatically unique nodes, relationships, or groups.')
            if nearest_sort is None or has_reorder_risk:
                insights.append(Insight('ORDER-NONDETERMINISTIC-SELECTION',
                    'The selected rows are not fully determined by a relevant ORDER BY',
                    'Pagination occurs without a guaranteed applicable ordering of the '
                    'input rows. Different valid row orders can select different occurrences.',
                    'possible', 'high', e.clause,
                    intent_check='Does the question require a deterministic top-k selection?'))
            else:
                insights.append(Insight('ORDER-TIES-LIMIT',
                    'Equal ranking keys can affect which rows pass LIMIT',
                    'Rows that compare equally on all explicit ORDER BY keys may be tied. '
                    'If a boundary cuts through a tie, the selected rows are not fully '
                    'determined by those keys alone.', 'possible', 'high', e.clause,
                    intent_check='Are ties or a deterministic tiebreaker important?'))
            insights.append(Insight('ORDER-BAG-LIMITATION',
                'SKIP/LIMIT needs stream rather than bag semantics',
                'The current symbolic multiplicity engine cannot derive which specific '
                'rows remain after slicing from an unordered bag expression.',
                'unsupported', 'medium', e.clause))
    if not details:
        details.append('The compiled query has no ORDER BY, SKIP, or LIMIT operation. '
                       'Result order is not guaranteed.')
    return Section('ordering', 'Ranking, pagination, and ordering',
        'Explain precisely which stage and units are sorted or limited, '
        'and distinguish ordering constraints from exact row selection.',
        tuple(details), tuple(insights))
