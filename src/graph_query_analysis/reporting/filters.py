"""Predicate dependency, row elimination, and NULL interpretation."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section, property_accesses
from graph_query_analysis.expressions import (AllOf, AnyOf, Comparison, FilterBag, IsNull,
                            MatchBag, Negation, ParameterReference, StringPredicate, TruthLiteral)


def _mentions_nullable_comparison(pred) -> bool:
    if isinstance(pred, (Comparison, StringPredicate)):
        return True
    if isinstance(pred, (AllOf, AnyOf)):
        return any(_mentions_nullable_comparison(x) for x in pred.conditions)
    if isinstance(pred, Negation):
        return _mentions_nullable_comparison(pred.condition)
    return False


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    for e in ctx.events:
        op = e.operator
        if isinstance(op, MatchBag):
            for n in op.nodes:
                for p in n.predicates:
                    details.append(f'Clause {e.clause} pattern-local condition on '
                                   f'{ctx.symbol(n.symbol, e.clause)}: {ctx.predicate(p, e.clause)}. '
                                   'It restricts eligible node assignments.')
            for r in op.relationships:
                for p in r.predicates:
                    details.append(f'Clause {e.clause} pattern-local condition on '
                                   f'{ctx.symbol(r.symbol, e.clause)}: {ctx.predicate(p, e.clause)}.')
        if not isinstance(op, FilterBag):
            continue
        p = op.predicate
        depends = ctx.describe_dependencies(p, e.clause)
        details.append(f'Clause {e.clause} WHERE: {ctx.predicate(p, e.clause)}.')
        details.append(f'  Selection dependencies: {depends}. '
                       'Rows pass only on TRUE; FALSE and NULL remove the row.')
        props = property_accesses(p)
        if props:
            details.append('  Properties used for selection: ' +
                           ', '.join(ctx.expression(x, e.clause) for x in props) + '.')
        if _mentions_nullable_comparison(p):
            insights.append(Insight('FILTER-NULL-ELIMINATION',
                'Comparisons may exclude rows with missing/NULL values',
                f'In clause {e.clause}, WHERE {ctx.predicate(p, e.clause)} retains only TRUE. '
                'If a required comparison is NULL and no other branch makes '
                'the overall predicate TRUE, the row is discarded.',
                'possible', 'medium', e.clause, evidence=(depends,)))
        insights.append(Insight('FILTER-SELECTION-LINEAGE', 'WHERE changes eligible bindings',
            f'The clause-{e.clause} predicate depends on {depends}; filtering only '
            'removes occurrences, but may eliminate entire entities or groups from '
            'later output.', 'entailed', 'medium', e.clause,
            evidence=(ctx.predicate(p, e.clause),)))
    if not details:
        details.append('No explicit row-filter or inline pattern predicate is present.')
    return Section('filters', 'Filters, eligibility, and NULL behavior',
        'Report predicate dependency and row exclusion without inventing graph data '
        'or claiming contradictions have been solved.', tuple(details), tuple(insights))
