"""Semantically concise, deterministic query fingerprint and section ordering."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section
from graph_query_analysis.expressions import (AggregateBag, DistinctBag, FilterBag,
                            MatchBag, ProjectBag, SliceBag, SortBag)
from graph_query_analysis.reporting import patterns, granularity, aggregates, filters, ordering, lineage, schema, scope


def create_sections(ctx: AnalysisContext) -> tuple[Section, ...]:
    # Reports remain independent; one module owns each semantic section.
    return (patterns.build(ctx), granularity.build(ctx), scope.build(ctx),
            aggregates.build(ctx), lineage.build(ctx), filters.build(ctx),
            ordering.build(ctx), schema.build(ctx))


def fingerprint(ctx: AnalysisContext) -> tuple[str, ...]:
    """Deterministic intent-comparison summary (no inferred natural-language intent)."""
    lines: list[str] = []
    matches = [e for e in ctx.events if isinstance(e.operator, MatchBag)]
    if matches:
        traversals = []
        for e in matches:
            for r in e.operator.relationships:
                if r.direction == 'INCOMING':
                    arrow = '<-'
                elif r.direction == 'BOTH':
                    arrow = '--'
                else:
                    arrow = '->'
                traversals.append(f'{ctx.symbol(r.left)} -[{ctx.symbol(r.symbol)}]'
                                  f'{arrow} {ctx.symbol(r.right)}')
        lines.append('Graph requirements: ' + ('; '.join(traversals) if traversals
                     else '; '.join(ctx.symbol(n.symbol) for e in matches
                                    for n in e.operator.nodes)) + '.')
    conditions = [e for e in ctx.events if isinstance(e.operator, FilterBag)]
    lines.append('Eligibility: ' + ('; '.join(ctx.predicate(e.operator.predicate) for e in conditions)
                 if conditions else 'mandatory MATCH requirements only') + '.')
    agg_event = next((e for e in reversed(ctx.events) if isinstance(e.operator, AggregateBag)), None)
    if agg_event:
        agg = agg_event.operator
        grouping = [ctx.expression(k.expression) for k in agg.grouping_keys]
        later_matches = any(isinstance(e.operator, MatchBag)
                            for e in ctx.events[ctx.events.index(agg_event) + 1:])
        lines.append('Result unit: ' + ('projected rows after a MATCH extending earlier aggregate groups'
                     if later_matches else 'group per (' + ', '.join(grouping) + ')'
                     if grouping else 'single global aggregate group') + '.')
        lines.append(f'Aggregate units at clause {agg_event.clause}: ' + '; '.join(ctx.expression(a.expression)
                     for a in agg.aggregate_computations) + '.')
    else:
        distinct = next((e for e in reversed(ctx.events)
                         if isinstance(e.operator, DistinctBag)), None)
        later_projection = bool(distinct and any(isinstance(e.operator, (ProjectBag, MatchBag))
                        for e in ctx.events[ctx.events.index(distinct) + 1:]))
        lines.append('Result unit: ' + ('projected bag row after an earlier DISTINCT at clause '
                     + str(distinct.clause) + '; later projections can merge values'
                     if later_projection else ('distinct projected tuple at clause ' + str(distinct.clause)
                     if distinct else 'projected bag row; equal output values can repeat')) + '.')
    columns = ctx.columns()
    lines.append('Output columns: ' + (', '.join((c.alias or ctx.expression(c.expression)) +
                 ' <- ' + ctx.expanded_expression(c.expression) for c in columns) or '(none)') + '.')
    sorts = [e for e in ctx.events if isinstance(e.operator, SortBag)]
    if sorts:
        s = sorts[-1].operator
        lines.append('Ranking: ' + ', '.join(ctx.expression(k.expression) +
                     (' DESC' if k.descending else ' ASC') for k in s.keys) + '.')
    slices = [e for e in ctx.events if isinstance(e.operator, SliceBag)]
    if slices:
        s = slices[-1].operator
        lines.append('Pagination: SKIP=' + (ctx.expression(s.skip) if s.skip else '0') +
                     ', LIMIT=' + (ctx.expression(s.limit) if s.limit else 'none') + '.')
    lines.append('Data access: none; all explanations are over possible compatible graphs.')
    return tuple(lines)


def prioritize(sections: tuple[Section, ...], limit: int = 18) -> tuple[Insight, ...]:
    """Suppress duplicates across sections while retaining clause evidence."""
    weights = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    candidates = [i for s in sections for i in s.insights]
    candidates.sort(key=lambda i: (weights[i.importance], i.clause or 0, i.rule_id))
    seen: set[tuple[str, int | None, tuple[str, ...]]] = set()
    out = []
    for item in candidates:
        key = (item.rule_id, item.clause, item.evidence)
        if key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit: break
    return tuple(out)
