"""Scope lifetime, aliases, shadowing, and information removal across WITH."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    history: dict[str, object] = {}
    for index in range(1, len(ctx.states)):
        before = {b.variable: b for b in ctx.states[index-1].scope}
        after = {b.variable: b for b in ctx.states[index].scope}
        kind = ctx.request.clauses[index-1]['type']
        new = tuple(n for n in after if n not in before)
        dropped = tuple(n for n in before if n not in after)
        changed = tuple(n for n in before if n in after and before[n].symbol != after[n].symbol)
        aliases = tuple((prior, fresh) for prior, b in before.items()
                        for fresh, later in after.items()
                        if prior != fresh and b.symbol == later.symbol)
        details.append(f'Clause {index} {kind}: in scope before = '
                       f'{", ".join(before) or "(none)"}; after = '
                       f'{", ".join(after) or "(none)"}.')
        if new:
            details.append('  Newly visible: ' + ', '.join(new) + '.')
        if dropped:
            details.append('  Removed from subsequent variable scope: ' + ', '.join(dropped) + '.')
        if aliases:
            details.append('  Identity-preserving aliases: ' +
                           ', '.join(f'{a} -> {b}' for a, b in aliases) + '.')
        if changed:
            details.append('  Visible name refers to a new symbolic slot: ' + ', '.join(changed) + '.')
        for name in new:
            if name in history and history[name] != after[name].symbol:
                insights.append(Insight('SCOPE-NAME-REINTRODUCED',
                    'A previously used variable name is a new binding',
                    f'{name} was used earlier, then left scope, and now denotes a different '
                    'symbolic slot. Its new binding does not imply identity with the earlier '
                    'graph entity.', 'entailed', 'high', index, (name,)))
        for name, b in after.items():
            history[name] = b.symbol
        if kind == 'With' and dropped:
            insights.append(Insight('SCOPE-WITH-LOSS', 'WITH removes nonprojected variables',
                f'After clause {index}, ' + ', '.join(dropped) +
                ' cannot be referenced unless rebound later. The old matching requirements '
                'may still affect row eligibility and multiplicity despite loss of the '
                'variable names.', 'entailed', 'medium', index, dropped))
    return Section('scope', 'Variable scope, aliasing, and information retention',
        'Track names as aliases of persistent symbolic identities; discarded names '
        'must not be mistaken for erased earlier match requirements.',
        tuple(details), tuple(insights))
