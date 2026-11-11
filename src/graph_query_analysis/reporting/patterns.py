"""Graph traversal, correlation, and relationship-witness identity explanations."""
from graph_query_analysis.reporting.core import AnalysisContext, Insight, Section, referenced_symbols
from graph_query_analysis.expressions import Comparison, FilterBag, MatchBag


def build(ctx: AnalysisContext) -> Section:
    details: list[str] = []
    insights: list[Insight] = []
    if not ctx.matches:
        return Section('patterns', 'Graph-pattern and join interpretation',
                       'No mandatory MATCH pattern was compiled.', (), ())

    previous: set = set()
    for event in ctx.events:
        if not isinstance(event.operator, MatchBag):
            continue
        op = event.operator
        current = {n.symbol for n in op.nodes}
        for r in op.relationships:
            current.update((r.left, r.right, r.symbol))
        shared = current & previous
        new = current - previous
        details.append(f'Clause {event.clause} MATCH:')
        for n in op.nodes:
            labels = ':'.join(n.labels) or '(any label)'
            details.append(f'  Node {ctx.symbol(n.symbol, event.clause)} requires {labels}; '
                           f'{len(n.predicates)} attached pattern predicate(s).')
        for r in op.relationships:
            labels = '|'.join(r.types) or '(any relationship type)'
            left, right = ctx.symbol(r.left, event.clause), ctx.symbol(r.right, event.clause)
            arrow = ('->' if r.direction == 'OUTGOING' else '<-' if r.direction == 'INCOMING' else '--')
            details.append(f'  Witness {ctx.symbol(r.symbol, event.clause)}: {left} {arrow}[{labels}] {right}; '
                           f'direction={r.direction}, attached conditions={len(r.predicates)}.')
        if shared:
            details.append('  Shared binding identities with earlier MATCH clauses: ' +
                           ', '.join(ctx.symbol(x, event.clause) for x in sorted(shared, key=ctx.symbol)) + '.')
            insights.append(Insight('PATTERN-CORRELATION', 'MATCH reuses an earlier binding',
                'This MATCH extends previous rows while keeping the same entity assigned to ' +
                ', '.join(ctx.symbol(s, event.clause) for s in sorted(shared, key=ctx.symbol)) +
                '; it does not independently choose new values for those symbols.',
                'entailed', 'medium', event.clause))
        elif previous:
            details.append('  No symbolic identity is shared with earlier MATCH clauses.')
            insights.append(Insight('PATTERN-INDEPENDENCE', 'No shared symbolic join identity',
                'This MATCH introduces assignments without reusing an earlier node or relationship '
                'symbol. Unless predicates correlate the two sets, their compatible assignments '
                'can combine multiplicatively. Distinct variables are not assumed to denote '
                'distinct physical nodes.', 'possible', 'high', event.clause,
                evidence=tuple(ctx.symbol(s, event.clause) for s in sorted(new, key=ctx.symbol)),
                intent_check='Should this pattern be linked to a previously matched variable?'))
        details.append('  New pattern slots: ' +
                       (', '.join(ctx.symbol(x, event.clause) for x in sorted(new, key=ctx.symbol)) or '(none)') + '.')
        # Even a single MATCH can contain disconnected comma-separated paths.
        # A later WHERE can correlate their properties without sharing identity.
        node_symbols = {n.symbol for n in op.nodes}
        node_symbols.update(r.left for r in op.relationships)
        node_symbols.update(r.right for r in op.relationships)
        parent = {s: s for s in node_symbols}
        def root(s):
            while parent[s] != s:
                s = parent[s]
            return s
        for r in op.relationships:
            parent[root(r.right)] = root(r.left)
        components = {root(s) for s in node_symbols}
        if len(components) > 1:
            details.append(f'  The MATCH contains {len(components)} structurally '
                           'disconnected node-pattern components. No node identity '
                           'is shared between those components.')
            insights.append(Insight('PATTERN-DISCONNECTED-COMPONENTS',
                'One MATCH contains structurally disconnected pattern components',
                'These components have no shared node symbols. Their possible '
                'assignments may combine multiplicatively, subject to WHERE/property '
                'correlations and within-MATCH relationship identity restrictions.',
                'possible', 'high', event.clause,
                intent_check='Are the independent graph components intended to be joined?'))
        if len(op.relationships) >= 2:
            edges = tuple(ctx.symbol(r.symbol, event.clause) for r in op.relationships)
            details.append('  Default within-MATCH distinct relationship identities: ' + ', '.join(edges))
            insights.append(Insight('PATTERN-EDGE-IDENTITY',
                'Relationship occurrences in one MATCH must be different',
                f'The {len(edges)} relationship occurrences in this MATCH must bind to '
                'different relationship identities in the modeled default fixed-length Cypher 25 '
                'fragment. This restriction is not automatically imposed between separate MATCH clauses.',
                'entailed', 'medium', event.clause, edges))
        if not op.relationships:
            details.append('  A node-only MATCH enumerates compatible node identities.')
        previous.update(current)

    if len(ctx.matches) > 1:
        details.append('Across MATCH clauses, row extensions preserve earlier assignments, '
                       'so multiplying compatible witnesses can affect later counts and sums.')
    return Section('patterns', 'Graph-pattern and join interpretation',
        'Actual node labels, relationship types, directions, symbol sharing, and witness '
        'restrictions introduced by each MATCH.', tuple(details), tuple(insights))
