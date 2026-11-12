"""Explain aggregate units using actual operator-stage witness dependencies.

Unlike a heuristic 'MATCH since latest DISTINCT', this section follows each
witness through projection, DISTINCT and aggregation. It separates influences
on *input occurrence counts* from influences on mere *group eligibility*.
"""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section, referenced_symbols
from graph_query_analysis.expressions import AggregateBag, AggregateCall, PropertyAccess, SymbolReference
from graph_query_analysis.cypher_types import infer_type


def _count_unit(ctx: AnalysisContext, agg: AggregateCall, clause: int) -> str:
    if agg.name == 'count' and agg.argument is None:
        return 'all input row occurrences, including duplicates (COUNT(*))'
    if agg.argument is None:
        return 'input row occurrences'
    arg = ctx.expression(agg.argument, clause)
    if agg.name == 'count':
        return ('distinct non-NULL values of ' if agg.distinct else
                'non-NULL occurrences of ') + arg
    if agg.distinct:
        return f'distinct non-NULL input values of {arg}'
    return f'non-NULL values of {arg}, weighted by input multiplicity'


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    aggregates = [e for e in ctx.events if isinstance(e.operator, AggregateBag)]
    if not aggregates:
        return Section('aggregates', 'Aggregation, counting units, and sensitivity',
                       'No aggregate operator appears in the supported compiled query.', (), ())

    for event in aggregates:
        op = event.operator
        input_effects = ctx.influence.before(op)
        groups = tuple(ctx.expression(k.expression, event.clause) for k in op.grouping_keys)
        details.append(f'Clause {event.clause}: grouping keys: ' +
                       (', '.join(groups) if groups else '(global aggregate)') + '.')
        details.append('  Every distinct present grouping-key tuple produces a group; '
                       'global aggregation produces one group even on empty input.')

        occurrence = input_effects.symbols('occurrences')
        distinct_keys = input_effects.symbols('distinct_keys')
        group_keys = input_effects.symbols('group_keys')
        eligibility = input_effects.symbols('eligibility')
        unknown = input_effects.symbols('unknown')
        if occurrence:
            details.append('  Raw occurrence-count influences: ' + ', '.join(
                ctx.symbol(s, event.clause) for s in occurrence) + '.')
        if distinct_keys:
            details.append('  Distinct retained-key influences (not raw duplicate inflation): ' +
                           ', '.join(ctx.symbol(s, event.clause) for s in distinct_keys) + '.')
        if eligibility:
            details.append('  Earlier witnesses reduced to eligibility-only influence: ' +
                           ', '.join(ctx.symbol(s, event.clause) for s in eligibility) + '.')
        if group_keys:
            details.append('  Earlier grouping-key influences: ' +
                           ', '.join(ctx.symbol(s, event.clause) for s in group_keys) + '.')
        if unknown:
            details.append('  The influence of preceding SKIP/LIMIT is not determined: ' +
                           ', '.join(ctx.symbol(s, event.clause) for s in unknown) + '.')

        for comp in op.aggregate_computations:
            call = comp.expression
            call_text = ctx.expression(call, event.clause)
            argument = ctx.expression(call.argument, event.clause)
            unit = _count_unit(ctx, call, event.clause)
            details.append(f'  {ctx.symbol(comp.symbol, event.clause)} = {call_text}: {unit}.')
            arg_sources = set(ctx.resolve(call.argument)) if call.argument is not None else set()
            group_sources = {sym for key in op.grouping_keys
                             for sym in ctx.resolve(key.expression)}
            inherited_value = tuple(dict.fromkeys(
                w for sym in referenced_symbols(call.argument)
                for w in input_effects.value_depends_on(sym))) if call.argument is not None else ()
            if inherited_value:
                names = tuple(ctx.symbol(s, event.clause) for s in inherited_value)
                details.append('    Earlier aggregate outputs carry value dependencies on ' +
                               ', '.join(names) + '; these do not imply direct '
                               'multiplication of the current input rows.')
                insights.append(Insight('AGG-PRIOR-AGGREGATE-VALUE-INFLUENCE',
                    'Earlier witness counts can influence a carried aggregate value',
                    f'{call_text} uses an input expression whose previously aggregated '
                    'value can depend on ' + ', '.join(names) +
                    '. This is a value dependency, not necessarily current-row duplication.',
                    'entailed', 'medium', event.clause, evidence=names))

            if call.name == 'count':
                insights.append(Insight('AGG-COUNT-UNIT', 'Exact counting unit',
                    f'{call_text} counts {unit}; DISTINCT on a node identity differs from '
                    'DISTINCT on one of its property values.', 'entailed', 'high', event.clause,
                    evidence=(f'grouping={groups or "global"}', f'argument={argument}')))
                if (not call.distinct and isinstance(call.argument, SymbolReference)
                        and (ctx.is_node(call.argument.symbol, event.clause)
                             or ctx.is_relationship(call.argument.symbol, event.clause))
                        and not infer_type(call.argument, event.before).nullable):
                    details.append('    The argument is known to be a non-NULL entity binding '
                                   'at this input stage, so every input occurrence is counted.')

            # DISTINCT on the aggregate *argument* cancels repeated occurrences
            # of equal argument values, but does not cancel their eligibility.
            if not call.distinct and call.name in ('count', 'sum', 'avg', 'collect'):
                other = [s for s in occurrence if s not in arg_sources and s not in group_sources]
                if other:
                    names = tuple(ctx.symbol(s, event.clause) for s in other)
                    insights.append(Insight('AGG-WITNESS-MULTIPLICATION',
                        f'{call_text} receives row multiplicity from other witnesses',
                        f'At clause {event.clause}, input occurrences for {call_text} can vary '
                        'with ' + ', '.join(names) + '. These witnesses are not the direct '
                        'argument identities. Multiple compatible assignments can repeat '
                        'contributions, while zero compatible assignments can exclude them.',
                        'possible', 'high', event.clause,
                        evidence=tuple(f'clause {next((m.clause for m in ctx.matches if any(r.symbol == s for r in m.operator.relationships) or any(n.symbol == s for n in m.operator.nodes)), "?")}: '
                                       + ctx.symbol(s, event.clause) for s in other),
                        intent_check='Should the aggregate count row assignments or distinct entities/values?'))
                # DISTINCT can preserve the *number of distinct retained keys*.
                relevant_keys = tuple(s for s in (*distinct_keys, *group_keys) if s not in group_sources)
                if relevant_keys:
                    keys = tuple(ctx.symbol(s, event.clause) for s in relevant_keys)
                    insights.append(Insight('AGG-DISTINCT-KEY-DEPENDENCY',
                        f'{call_text} is sensitive to retained distinct keys',
                        'An earlier DISTINCT or grouping formed separate key tuples, and '
                        'the number of *different* retained tuples can still influence '
                        f'{call_text}. Key dependencies: ' + ', '.join(keys) + '.',
                        'possible', 'high', event.clause, evidence=keys))
            elif call.distinct:
                qualifying_witnesses = [s for s in occurrence
                                        if s not in arg_sources and s not in group_sources]
                if qualifying_witnesses:
                    witness_names = tuple(ctx.symbol(s, event.clause)
                                          for s in qualifying_witnesses)
                    insights.append(Insight('AGG-DISTINCT-QUALIFICATION',
                        'Additional matches can qualify DISTINCT aggregate values',
                        f'{call_text} ignores duplicate copies of equal argument values, '
                        'but ' + ', '.join(witness_names) + ' can determine whether those '
                        'values contribute at all. This is a selection effect, not '
                        'raw multiplicity inflation.', 'possible', 'high', event.clause,
                        evidence=witness_names))
                # When a witness is not in the aggregate argument, repeated rows
                # can only influence which values occur, not repeat equal values.
                details.append('    DISTINCT on the aggregate argument removes equal '
                               'argument values; extra relationship witnesses can affect '
                               'qualification, but cannot inflate the aggregate by repeating '
                               'one identical argument value.')
                if distinct_keys and any(s in arg_sources for s in distinct_keys):
                    details.append('    Retained distinct-key dependencies may influence '
                                   'which different argument values are present.')

            if eligibility and input_effects.reduction:
                reduction_clause, reduction_kind = input_effects.reduction
                names = tuple(ctx.symbol(s, event.clause) for s in eligibility)
                details.append('    Eligibility dependencies from earlier collapsed witnesses: ' +
                               ', '.join(names) + '. They are not direct raw row multipliers.')
                insights.append(Insight('AGG-ELIGIBILITY-AFTER-REDUCTION',
                    f'Earlier witnesses can affect eligibility after {reduction_kind}',
                    'Witnesses ' + ', '.join(names) + f' were reduced at clause {reduction_clause} '
                    f'by {reduction_kind}. Their prior assignment counts must not be multiplied into '
                    f'{call_text}; matching them can still be required for an input group/value to exist.',
                    'entailed', 'high', event.clause, evidence=(call_text, *names)))

            if call.name == 'avg' and not call.distinct and occurrence:
                insights.append(Insight('AGG-AVG-WEIGHTING', 'AVG weights row occurrences',
                    f'{call_text} weights each non-NULL input according to its number of '
                    'occurrences; unequal multiplicity factors can change the average.',
                    'possible', 'medium', event.clause))
            if call.name in ('min', 'max'):
                details.append('    Duplicating an existing non-NULL value alone cannot '
                               'change MIN/MAX, although excluding candidates can.')
                insights.append(Insight('AGG-EXTREME-NOT-ARGMAX',
                    'MIN/MAX computes an extreme value, not the attaining entity',
                    f'{call_text} returns an extreme value within each group; it does not '
                    'independently select the node or relationship attaining that value.',
                    'entailed', 'medium', event.clause))
            if call.name == 'collect':
                insights.append(Insight('AGG-COLLECT-ORDER', 'COLLECT order is not established',
                    'Unordered bag multiplicities do not determine list element order; '
                    'equal non-NULL values can occur more than once without DISTINCT.',
                    'unsupported', 'medium', event.clause))
            if call.distinct:
                argument_type = infer_type(call.argument, event.before).name
                if argument_type in ('NODE', 'RELATIONSHIP'):
                    explanation = (f'{call_text} deduplicates {argument} by '
                        f'{argument_type.lower()} identities. Different entities remain distinct '
                        'even when all their property values are equal.')
                    intent_check = None
                elif isinstance(ctx.origin_expression(call.argument), PropertyAccess):
                    explanation = (f'{call_text} deduplicates the property values of {argument}. '
                        'Equal property values from different nodes may collapse.')
                    intent_check = 'Should distinctness apply to property values or entity identities?'
                else:
                    explanation = (f'{call_text} deduplicates non-NULL values of {argument}. '
                        'Entity values compare by identity; other values use Cypher value equality.')
                    intent_check = None
                insights.append(Insight('AGG-DISTINCT-ARGUMENT', 'DISTINCT comparison follows the argument type',
                    explanation, 'entailed', 'high', event.clause, intent_check=intent_check))
            if call.argument is not None:
                details.append('    NULL handling: aggregate arguments that evaluate to NULL '
                               'are ignored by this aggregate.')

        if not op.grouping_keys:
            insights.append(Insight('AGG-EMPTY-GLOBAL', 'Global aggregate retains an empty-input group',
                'Without grouping keys, a global aggregate produces one group even '
                'when MATCH/WHERE yields no rows. Its aggregate values follow '
                'the corresponding empty-input definitions.', 'entailed', 'medium', event.clause))
        if any(not (isinstance(k.expression, SymbolReference)
                    and (ctx.is_node(k.expression.symbol, event.clause)
                         or ctx.is_relationship(k.expression.symbol, event.clause)))
               for k in op.grouping_keys):
            insights.append(Insight('AGG-GROUP-VALUE-NOT-IDENTITY',
                'Value grouping can merge distinct entity identities',
                'Grouping on ' + ', '.join(groups) + ' compares expression values. '
                'Unless uniqueness is separately established, different graph '
                'entities may share the same grouping key.', 'possible', 'high', event.clause,
                intent_check='Should grouping be by entity identity rather than a property?'))

    return Section('aggregates', 'Aggregation, counting units, and sensitivity',
        'Analyze aggregate units and distinguish raw multiplicity, DISTINCT-key '
        'diversity, and eligibility-only witness effects at each operator boundary.',
        tuple(details), tuple(insights))
