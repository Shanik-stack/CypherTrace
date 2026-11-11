"""Operator-derived witness influences used by reporting sections.

This is a *conservative dataflow analysis*, not a second query evaluator.
A witness may influence (a) whether a row/group exists, (b) how many input
occurrences contribute, or (c) which distinct projected keys exist. The modes
are derived by walking the compiled operator chain in order, especially across
Project/Distinct/Aggregate boundaries. No graph records are required.
"""
from dataclasses import dataclass
from typing import Literal

from graph_query_analysis.expressions import (AggregateBag, DistinctBag, ExtendBag, FilterBag,
                           MatchBag, ProjectBag, SliceBag)
from graph_query_analysis.model import Symbol

EffectMode = Literal['occurrences', 'distinct_keys', 'eligibility', 'unknown']


@dataclass(frozen=True)
class InfluenceSnapshot:
    """One operator boundary: effects for witnesses introduced so far.

    - occurrences: compatible assignments can change input row multiplicities.
    - distinct_keys: witness affects retained distinct key values/identities;
      an ensuing COUNT(*) can still count several such keys.
    - eligibility: a witness can determine whether a row/key survives, but
      earlier repetition has already been collapsed by a support/group boundary.
    - unknown: effects after an unsupported stream-slicing operation.

    A witness can affect eligibility in ALL modes, including 'occurrences'.
    """
    modes: tuple[tuple[Symbol, EffectMode], ...] = ()
    row_sources: tuple[tuple[Symbol, tuple[Symbol, ...]], ...] = ()
    selection_clauses: tuple[int, ...] = ()
    # Influences stored inside values computed by earlier aggregations. They
    # must not be mistaken for current-row duplicate contributions.
    value_sources: tuple[tuple[Symbol, tuple[Symbol, ...]], ...] = ()

    def mode(self, symbol: Symbol) -> EffectMode | None:
        return dict(self.modes).get(symbol)

    def symbols(self, *modes: EffectMode) -> tuple[Symbol, ...]:
        return tuple(s for s, mode in self.modes if mode in modes)

    def depends_on(self, symbol: Symbol) -> tuple[Symbol, ...]:
        return dict(self.row_sources).get(symbol, ())

    def value_depends_on(self, symbol: Symbol) -> tuple[Symbol, ...]:
        return dict(self.value_sources).get(symbol, ())


class InfluenceTimeline:
    """Summarize influences once, reusing the exact compiled operator objects."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._before: dict[int, InfluenceSnapshot] = {}
        self._after: dict[int, InfluenceSnapshot] = {}
        modes: dict[Symbol, EffectMode] = {}
        rows: dict[Symbol, tuple[Symbol, ...]] = {}
        selections: list[int] = []
        carried_values: dict[Symbol, tuple[Symbol, ...]] = {}
        from graph_query_analysis.reporting.core import referenced_symbols

        def previous_value_sources(expr):
            return tuple(dict.fromkeys(w for sym in referenced_symbols(expr)
                                       for w in carried_values.get(sym, ())))

        def snapshot() -> InfluenceSnapshot:
            return InfluenceSnapshot(tuple(modes.items()), tuple(rows.items()),
                                     tuple(dict.fromkeys(selections)),
                                     tuple(carried_values.items()))

        for event in ctx.events:
            op = event.operator
            self._before[id(op)] = snapshot()
            if isinstance(op, MatchBag):
                # A pattern on an already bound slot only restricts eligibility;
                # it does not create a new assignment dimension for that slot.
                pattern_slots = [*(n.symbol for n in op.nodes)]
                related_nodes = set()
                for rel in op.relationships:
                    pattern_slots.extend((rel.left, rel.right, rel.symbol))
                    related_nodes.update((rel.left, rel.right))
                relationship_slots = {rel.symbol for rel in op.relationships}
                # A previously enumerated standalone node stops being an
                # independent multiplicity dimension when a mandatory edge
                # constrains it to that edge's endpoint. Its prior existence
                # constraints still affect eligibility.
                for endpoint in related_nodes:
                    if endpoint in modes and modes[endpoint] != 'unknown':
                        modes[endpoint] = 'eligibility'
                for slot in dict.fromkeys(pattern_slots):
                    if slot not in rows:
                        rows[slot] = (slot,)
                        # Once a fixed relationship identity and its direction
                        # are chosen, its endpoint nodes are not independent
                        # Cartesian-product dimensions. Report relationship
                        # witnesses instead of also blaming their endpoints.
                        if slot in relationship_slots or slot not in related_nodes:
                            modes[slot] = 'occurrences'
                selections.append(event.clause)
            elif isinstance(op, ExtendBag):
                updated = {col.symbol: previous_value_sources(col.expression)
                           for col in op.columns}
                for col in op.columns:
                    rows[col.symbol] = ctx.resolve(col.expression)
                carried_values.update(updated)
            elif isinstance(op, ProjectBag):
                updated = {col.symbol: previous_value_sources(col.expression)
                           for col in op.columns}
                rows = {col.symbol: ctx.resolve(col.expression) for col in op.columns}
                carried_values = updated
            elif isinstance(op, DistinctBag):
                key_dependencies = set().union(*map(set, rows.values())) if rows else set()
                for witness in modes:
                    # A retained node/edge value or property-derived key can
                    # vary with the witness even after DISTINCT removes copies.
                    modes[witness] = ('distinct_keys' if witness in key_dependencies
                                      else 'eligibility')
            elif isinstance(op, AggregateBag):
                # An old MATCH may no longer duplicate *rows* after aggregation,
                # while its multiplicity may be encoded in COUNT/SUM/AVG values.
                # Keep that dependence attached to computed aggregate symbols.
                computed = {}
                for comp in op.aggregate_computations:
                    inherited = previous_value_sources(comp.expression)
                    computed[comp.symbol] = tuple(dict.fromkeys(
                        (*modes.keys(), *inherited)))
                key_values = {key.symbol: previous_value_sources(key.expression)
                              for key in op.grouping_keys}
                all_values = {**carried_values, **computed, **key_values}
                updated = {col.symbol: tuple(dict.fromkeys(
                    w for sym in referenced_symbols(col.expression)
                    for w in all_values.get(sym, ()))) for col in op.columns}
                for witness in modes:
                    modes[witness] = 'eligibility'
                rows = {col.symbol: ctx.resolve(col.expression) for col in op.columns}
                carried_values = updated
            elif isinstance(op, FilterBag):
                selections.append(event.clause)
            elif isinstance(op, SliceBag):
                for witness in modes:
                    modes[witness] = 'unknown'
            self._after[id(op)] = snapshot()

    def before(self, operator) -> InfluenceSnapshot:
        return self._before[id(operator)]

    def after(self, operator) -> InfluenceSnapshot:
        return self._after[id(operator)]

    def final(self) -> InfluenceSnapshot:
        return (self.after(self.ctx.events[-1].operator) if self.ctx.events
                else InfluenceSnapshot())
