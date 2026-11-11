"""Row-unit, distinctness, and information-loss explanation across clauses."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section
from graph_query_analysis.expressions import (AggregateBag, DistinctBag, ExtendBag, FilterBag,
                            MatchBag, ProjectBag, SliceBag, SortBag, SymbolReference)


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    row_slots: tuple = ()
    unique: tuple | None = None
    last_distinct: tuple | None = None
    recent_projection_dropped: tuple = ()
    for e in ctx.events:
        op = e.operator
        tag = f'Clause {e.clause}, {type(op).__name__}'
        if isinstance(op, MatchBag):
            added = tuple(dict.fromkeys([*(n.symbol for n in op.nodes),
                         *(r.symbol for r in op.relationships)]))
            row_slots = tuple(dict.fromkeys((*row_slots, *added)))
            details.append(f'{tag}: each row corresponds to one compatible assignment to '
                           + (', '.join(ctx.symbol(s, e.clause) for s in row_slots) or '(no variables)') + '.')
            before_effects = ctx.influence.before(op)
            after_effects = ctx.influence.after(op)
            new_expansion = tuple(s for s in after_effects.symbols('occurrences')
                                  if before_effects.mode(s) is None)
            if last_distinct is not None and new_expansion:
                insights.append(Insight('GRAIN-EXPANDED-AFTER-DISTINCT',
                    'A later MATCH can reintroduce duplicate projected values',
                    'After a previous DISTINCT on ' + ', '.join(last_distinct) + ', this MATCH '
                    'introduces new assignment dimensions (' +
                    ', '.join(ctx.symbol(s, e.clause) for s in new_expansion) +
                    '). The earlier uniqueness guarantee does not automatically hold '
                    'for subsequent projected rows.', 'possible', 'high', e.clause))
                last_distinct = None
            if new_expansion:
                unique = None
        elif isinstance(op, ExtendBag):
            row_slots = tuple(dict.fromkeys((*row_slots, *(c.symbol for c in op.columns))))
            details.append(f'{tag}: computes aliases while retaining earlier witness slots.')
        elif isinstance(op, ProjectBag):
            prior = row_slots
            row_slots = tuple(dict.fromkeys(c.symbol for c in op.columns))
            dropped = tuple(s for s in prior if s not in row_slots)
            recent_projection_dropped = dropped
            keys = tuple((c.alias or ctx.expression(c.expression, e.clause)) for c in op.columns)
            details.append(f'{tag}: projected tuple = ({", ".join(keys)}); '
                           f'discarded slots = {", ".join(ctx.symbol(s, e.clause) for s in dropped) or "none"}. '
                           'Rows with equal projected tuples add their multiplicities.')
            if unique is not None:
                # A projection that preserves every key symbol still preserves uniqueness;
                # otherwise the guarantee cannot be propagated.
                pass_through = {c.expression.symbol for c in op.columns
                                if isinstance(c.expression, SymbolReference)}
                if all(s in pass_through for s in unique):
                    details.append('  Previous unique identity tuple is retained in the projection.')
                else:
                    old = ', '.join(ctx.symbol(s, e.clause) for s in unique)
                    insights.append(Insight('GRAIN-DISTINCT-KEY-LOSS',
                        'Projection discards part of an earlier uniqueness key',
                        f'The previous DISTINCT/grouping key ({old}) is not fully retained as '
                        'identity-valued output columns. Different earlier rows may now project '
                        'to equal values; uniqueness of the new tuple is not established.',
                        'possible', 'high', e.clause,
                        intent_check='Does the requested result require unique output values or entities?'))
                    unique = None
        elif isinstance(op, DistinctBag):
            unique = row_slots
            last_distinct = tuple(ctx.symbol(s, e.clause) for s in row_slots)
            details.append(f'{tag}: support of the current projected tuple '
                           f'({", ".join(last_distinct)}). At this stage exactly one row '
                           'occurrence per distinct tuple value survives.')
            hidden = tuple(s for s in recent_projection_dropped
                           if ctx.is_node(s, e.clause) or ctx.is_relationship(s, e.clause))
            if hidden:
                discarded_desc = ', '.join(ctx.symbol(s, e.clause) for s in hidden)
                details.append(f'  Hidden witnesses {discarded_desc} no longer determine the '
                               'number of copies of each retained tuple. They still determine '
                               'whether that tuple has at least one compatible witness.')
                insights.append(Insight('GRAIN-EXISTENTIAL-COLLAPSE',
                    'DISTINCT changes witness counting into existence for retained tuples',
                    'For each projected tuple, compatible assignments to ' + discarded_desc +
                    ' establish whether the tuple exists. Their number no longer sets the '
                    'tuple multiplicity after DISTINCT; at least one qualifying assignment '
                    'produces exactly one occurrence.', 'entailed', 'high', e.clause,
                    evidence=tuple(ctx.symbol(s, e.clause) for s in hidden)))
            insights.append(Insight('GRAIN-DISTINCT', 'The deduplication key is the entire projected tuple',
                'DISTINCT operates on output values ' + ', '.join(last_distinct) +
                '. Node and relationship columns compare by identity; property expressions '
                'compare by their values. This does not promise later output uniqueness.',
                'entailed', 'medium', e.clause, last_distinct))
        elif isinstance(op, AggregateBag):
            unique = tuple(k.symbol for k in op.grouping_keys)
            last_distinct = None
            row_slots = tuple(c.symbol for c in op.columns)
            grouping = tuple(ctx.expression(k.expression, e.clause) for k in op.grouping_keys)
            details.append(f'{tag}: output row unit is '
                           + (f'one group per distinct ({", ".join(grouping)})' if grouping
                              else 'one global aggregate group') +
                           '. Group equality compares values (including NULL group values).')
            insights.append(Insight('GRAIN-GROUP-KEY', 'Aggregation changes the unit of results',
                'Aggregate output represents groups keyed by ' +
                (', '.join(grouping) if grouping else '(global grouping)') +
                ', not individual MATCH assignments. Grouping by properties is not the same '
                'as grouping by node identity.', 'entailed', 'high', e.clause, grouping))
        elif isinstance(op, FilterBag):
            details.append(f'{tag}: retains or removes occurrences; no new row identity.')
        elif isinstance(op, SortBag):
            details.append(f'{tag}: changes order, not row unit or multiplicity.')
        elif isinstance(op, SliceBag):
            details.append(f'{tag}: selects occurrences from an ordered/unordered stream; '
                           'which values survive may depend on ordering and ties.')
            unique = None
    if row_slots:
        details.append('Final symbolic output slots: ' + ', '.join(ctx.symbol(s, len(ctx.request.clauses)) for s in row_slots) + '.')
    return Section('granularity', 'Row granularity, DISTINCT, and information loss',
        'Trace what one row represents after each operation and which identity/value '
        'distinctions have been collapsed or discarded.', tuple(details), tuple(insights))
