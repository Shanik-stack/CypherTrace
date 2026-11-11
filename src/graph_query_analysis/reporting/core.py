"""Shared, source-preserving facts for deterministic Cypher explanation.

This is an inspection layer over existing compiled operator objects. It is NOT
another query semantics, solver, graph, row evaluator or AST representation.
"""
from dataclasses import dataclass, fields, is_dataclass
from typing import Literal, Sequence

from graph_query_analysis.expressions import (AggregateBag, AggregateCall, AllOf, AnyOf, Comparison,
    Arithmetic, DistinctBag, ExtendBag, FilterBag, FunctionCall, HasLabel, IsNull,
    MatchBag, Negation, NodeInGraph, ParameterReference, Predicate, ProjectBag,
    Projection, PropertyAccess, RelationshipHolds, SameValue, SliceBag, SortBag,
    SymbolReference, TruthLiteral, ValueExpression, ValueLiteral)
from graph_query_analysis.model import (AnalysisRequest, BagExpression, NodeSymbol,
                     RelationshipSymbol, Symbol, SymbolicState, ValueSymbol) 
from graph_query_analysis.algebraic_translation import format_predicate, format_value

Certainty = Literal['entailed', 'possible', 'unknown', 'unsupported']
Importance = Literal['critical', 'high', 'medium', 'low']

@dataclass(frozen=True)
class Insight:
    rule_id: str
    title: str
    explanation: str
    certainty: Certainty
    importance: Importance = 'medium'
    clause: int | None = None
    evidence: tuple[str, ...] = ()
    intent_check: str | None = None

@dataclass(frozen=True)
class Section:
    key: str
    title: str
    overview: str
    details: tuple[str, ...]
    insights: tuple[Insight, ...] = ()

@dataclass(frozen=True)
class Event:
    clause: int
    clause_type: str
    operator: BagExpression
    before: SymbolicState
    after: SymbolicState


def new_operators(old: BagExpression, new: BagExpression) -> tuple[BagExpression, ...]:
    """Only the operators introduced by the current clause, by object identity."""
    ops: list[BagExpression] = []
    current = new
    while current is not old:
        ops.append(current)
        current = getattr(current, 'source', None)
        if current is None:
            raise ValueError('Compiled bag chain does not extend the previous state')
    return tuple(reversed(ops))


def referenced_symbols(value: object) -> tuple[Symbol, ...]:
    """Traverse syntax/semantic values, preserving symbol identities and order."""
    result: list[Symbol] = []
    def visit(x: object) -> None:
        if isinstance(x, SymbolReference):
            result.append(x.symbol)
            return
        if isinstance(x, (NodeSymbol, RelationshipSymbol, ValueSymbol)):
            result.append(x)
            return
        if isinstance(x, (ValueLiteral, ParameterReference)):
            return
        if is_dataclass(x):
            for f in fields(x):
                visit(getattr(x, f.name))
        elif isinstance(x, (tuple, list)):
            for child in x:
                visit(child)
    visit(value)
    return tuple(dict.fromkeys(result))


def property_accesses(value: object) -> tuple[PropertyAccess, ...]:
    result: list[PropertyAccess] = []
    def visit(x: object) -> None:
        if isinstance(x, PropertyAccess):
            result.append(x)
        if is_dataclass(x):
            for f in fields(x):
                visit(getattr(x, f.name))
        elif isinstance(x, (tuple, list)):
            for item in x: visit(item)
    visit(value)
    return tuple(dict.fromkeys(result))


def aggregate_calls(value: object) -> tuple[AggregateCall, ...]:
    result: list[AggregateCall] = []
    def visit(x: object) -> None:
        if isinstance(x, AggregateCall):
            result.append(x)
            return
        if is_dataclass(x):
            for f in fields(x): visit(getattr(x, f.name))
        elif isinstance(x, (tuple, list)):
            for child in x: visit(child)
    visit(value)
    return tuple(result)


class AnalysisContext:
    """One immutable compiled query plus derived, stage-aware descriptions."""
    def __init__(self, request: AnalysisRequest, states: Sequence[SymbolicState]):
        self.request = request
        self.states = tuple(states)
        if len(self.states) != len(request.clauses) + 1:
            raise ValueError('One initial state plus one state per clause is required')
        events: list[Event] = []
        for i, clause in enumerate(request.clauses, 1):
            for op in new_operators(self.states[i-1].bag, self.states[i].bag):
                events.append(Event(i, clause['type'], op, self.states[i-1], self.states[i]))
        self.events = tuple(events)
        self.matches = tuple(e for e in events if isinstance(e.operator, MatchBag))
        # Meaning of symbol names is tracked by object identity, not text: a
        # symbol reused across clauses remains the same; rebinding is new.
        self._aliases: dict[Symbol, list[str]] = {}
        for state in self.states:
            for b in state.scope:
                if b.variable not in self._aliases.setdefault(b.symbol, []):
                    self._aliases[b.symbol].append(b.variable)
        self._definitions: dict[Symbol, tuple[ValueExpression, int]] = {}
        for e in events:
            op = e.operator
            if isinstance(op, (ProjectBag, ExtendBag)):
                for col in op.columns:
                    if col.symbol != getattr(col.expression, 'symbol', None):
                        self._definitions[col.symbol] = (col.expression, e.clause)
            if isinstance(op, AggregateBag):
                for key in op.grouping_keys:
                    self._definitions[key.symbol] = (key.expression, e.clause)
                for agg in op.aggregate_computations:
                    self._definitions[agg.symbol] = (agg.expression, e.clause)
        # Snapshot requirements AFTER each clause. A label introduced in a
        # later MATCH must never appear in an earlier intermediate-state report.
        nodes: dict[Symbol, set[str]] = {}
        relationships: dict[Symbol, set[str]] = {}
        self._nodes_by_clause: dict[int, dict[Symbol, frozenset[str]]] = {0: {}}
        self._rels_by_clause: dict[int, dict[Symbol, frozenset[str]]] = {0: {}}
        for clause_index in range(1, len(self.states)):
            for event in (e for e in self.matches if e.clause == clause_index):
                for node in event.operator.nodes:
                    nodes.setdefault(node.symbol, set()).update(node.labels)
                for rel in event.operator.relationships:
                    relationships.setdefault(rel.symbol, set()).update(rel.types)
            self._nodes_by_clause[clause_index] = {
                sym: frozenset(labels) for sym, labels in nodes.items()}
            self._rels_by_clause[clause_index] = {
                sym: frozenset(types) for sym, types in relationships.items()}
        # Final maps retained for compatibility; stage-specific reports MUST
        # call labels_at/types_at/symbol(..., at_clause=...) instead.
        self._nodes = self._nodes_by_clause[len(self.states) - 1]
        self._relationships = self._rels_by_clause[len(self.states) - 1]
        from graph_query_analysis.reporting.influence import InfluenceTimeline
        self.influence = InfluenceTimeline(self)

    def labels_at(self, sym: Symbol, clause: int | None = None) -> frozenset[str]:
        index = len(self.states) - 1 if clause is None else clause
        return self._nodes_by_clause[index].get(sym, frozenset())

    def types_at(self, sym: Symbol, clause: int | None = None) -> frozenset[str]:
        index = len(self.states) - 1 if clause is None else clause
        return self._rels_by_clause[index].get(sym, frozenset())

    def kind_at(self, sym: Symbol, clause: int | None = None) -> str:
        """Preserve node-valued computed symbols, not only NodeSymbol slots."""
        index = len(self.states) - 1 if clause is None else clause
        for state in reversed(self.states[:index + 1]):
            for binding in state.scope:
                if binding.symbol == sym and binding.value_kind != 'UNKNOWN':
                    return binding.value_kind
        if isinstance(sym, NodeSymbol):
            return 'NODE'
        if isinstance(sym, RelationshipSymbol):
            return 'RELATIONSHIP'
        return 'UNKNOWN'

    def is_node(self, sym: Symbol, clause: int | None = None) -> bool:
        return (self.kind_at(sym, clause) == 'NODE' or
                sym in self._nodes_by_clause[len(self.states) - 1 if clause is None else clause])

    def is_relationship(self, sym: Symbol, clause: int | None = None) -> bool:
        return (self.kind_at(sym, clause) == 'RELATIONSHIP' or
                sym in self._rels_by_clause[len(self.states) - 1 if clause is None else clause])

    def name(self, sym: Symbol, at_clause: int | None = None) -> str:
        if at_clause is not None:
            for state in reversed(self.states[:at_clause+1]):
                for binding in state.scope:
                    if binding.symbol == sym:
                        return binding.variable
        if sym in self._aliases:
            return self._aliases[sym][0]
        text = sym.name
        if '\x00' in text:
            # Anonymous symbols are not user-visible variables, but keeping a
            # stable identity per occurrence avoids collapsing different slots.
            return text.replace('\x00', '@')
        return text

    def symbol(self, sym: Symbol, at_clause: int | None = None) -> str:
        name = self.name(sym, at_clause)
        if self.is_node(sym, at_clause):
            labels = sorted(self.labels_at(sym, at_clause))
            return name + (':' + ':'.join(labels) if labels else '')
        if self.is_relationship(sym, at_clause):
            types = sorted(self.types_at(sym, at_clause))
            return name + (':' + '|'.join(types) if types else '')
        return name

    def expression(self, expr: ValueExpression | None,
                   at_clause: int | None = None) -> str:
        if expr is None:
            return '*'
        if isinstance(expr, SymbolReference):
            return self.name(expr.symbol, at_clause)
        if isinstance(expr, PropertyAccess):
            return self.expression(expr.owner, at_clause) + '.' + expr.property_name
        if isinstance(expr, AggregateCall):
            return f"{expr.name.upper()}({'DISTINCT ' if expr.distinct else ''}{self.expression(expr.argument, at_clause)})"
        if isinstance(expr, FunctionCall):
            return f"{expr.name}({', '.join(self.expression(a, at_clause) for a in expr.arguments)})"
        if isinstance(expr, ValueLiteral):
            return repr(expr.value)
        if isinstance(expr, ParameterReference):
            return '$' + expr.name
        return format_value(expr).replace('\x00', '@')

    def origin_expression(self, expr: ValueExpression,
                          seen: frozenset[Symbol] = frozenset()) -> ValueExpression:
        """Resolve projected symbol references back to source expressions.

        Used for lineage, never for changing the original bag operator tree.
        """
        if isinstance(expr, SymbolReference):
            if expr.symbol in self._definitions and expr.symbol not in seen:
                return self.origin_expression(self._definitions[expr.symbol][0],
                                              seen | {expr.symbol})
            return expr
        if isinstance(expr, PropertyAccess):
            return PropertyAccess(self.origin_expression(expr.owner, seen), expr.property_name)
        if isinstance(expr, AggregateCall):
            return AggregateCall(expr.name,
                                 self.origin_expression(expr.argument, seen)
                                 if expr.argument is not None else None, expr.distinct)
        if isinstance(expr, FunctionCall):
            return FunctionCall(expr.name, tuple(self.origin_expression(x, seen)
                                                  for x in expr.arguments))
        if isinstance(expr, Arithmetic):
            return Arithmetic(expr.operator, self.origin_expression(expr.left, seen),
                              self.origin_expression(expr.right, seen))
        return expr

    def expanded_expression(self, expr: ValueExpression | None,
                            seen: frozenset[Symbol] = frozenset()) -> str:
        """Show the original value or aggregate behind projected symbolic slots."""
        if expr is None:
            return '*'
        if isinstance(expr, SymbolReference):
            if expr.symbol in self._definitions and expr.symbol not in seen:
                return self.expanded_expression(self._definitions[expr.symbol][0],
                                                seen | {expr.symbol})
            return self.name(expr.symbol)
        if isinstance(expr, PropertyAccess):
            return self.expanded_expression(expr.owner, seen) + '.' + expr.property_name
        if isinstance(expr, AggregateCall):
            return f"{expr.name.upper()}({'DISTINCT ' if expr.distinct else ''}{self.expanded_expression(expr.argument, seen)})"
        if isinstance(expr, FunctionCall):
            return f"{expr.name}({', '.join(self.expanded_expression(a, seen) for a in expr.arguments)})"
        return self.expression(expr)

    def predicate(self, pred: Predicate, at_clause: int | None = None) -> str:
        if isinstance(pred, Comparison):
            return (f'{self.expression(pred.left, at_clause)} {pred.operator} '
                    f'{self.expression(pred.right, at_clause)}')
        if isinstance(pred, SameValue):
            return (f'same({self.expression(pred.left, at_clause)}, '
                    f'{self.expression(pred.right, at_clause)})')
        if isinstance(pred, IsNull):
            return (f'{self.expression(pred.expression, at_clause)} IS '
                    f'{"NOT " if pred.negated else ""}NULL')
        if isinstance(pred, AllOf):
            return '(' + ' AND '.join(self.predicate(x, at_clause) for x in pred.conditions) + ')'
        if isinstance(pred, AnyOf):
            return '(' + ' OR '.join(self.predicate(x, at_clause) for x in pred.conditions) + ')'
        if isinstance(pred, Negation):
            return f'NOT ({self.predicate(pred.condition, at_clause)})'
        if isinstance(pred, HasLabel):
            return f'{self.name(pred.node, at_clause)}:{pred.label}'
        if isinstance(pred, NodeInGraph):
            return f'node_in_graph({self.name(pred.node, at_clause)})'
        if isinstance(pred, TruthLiteral):
            return str(pred.value).upper()
        return format_predicate(pred).replace('\x00', '@')

    def resolve(self, expr: object, *, seen: frozenset[Symbol] = frozenset()) -> tuple[Symbol, ...]:
        """Original input symbols that feed an expression, through aliases.

        Never expands an aggregate into nonexistent concrete rows. Recursive
        references are guarded by object identity.
        """
        out: list[Symbol] = []
        for sym in referenced_symbols(expr):
            if sym in seen:
                continue
            definition = self._definitions.get(sym)
            if definition is None:
                out.append(sym)
            else:
                out.extend(self.resolve(definition[0], seen=seen | {sym}))
        return tuple(dict.fromkeys(out))

    def describe_dependencies(self, expr: object, at_clause: int | None = None) -> str:
        symbols = self.resolve(expr)
        return ", ".join(self.symbol(s, at_clause) for s in symbols) if symbols else "(constant or parameter)"

    def columns(self) -> tuple[Projection, ...]:
        for e in reversed(self.events):
            if isinstance(e.operator, (ProjectBag, AggregateBag)):
                return e.operator.columns
        return ()

    def all_witnesses(self, through_clause: int | None = None) -> tuple[Symbol, ...]:
        witnesses = []
        for e in self.matches:
            if through_clause is not None and e.clause > through_clause:
                break
            for n in e.operator.nodes:
                witnesses.append(n.symbol)
            for rel in e.operator.relationships:
                witnesses.append(rel.symbol)
        return tuple(dict.fromkeys(witnesses))

    def last_row_reduction(self, clause: int) -> Event | None:
        return next((e for e in reversed(self.events)
                     if e.clause <= clause and isinstance(e.operator, (DistinctBag, AggregateBag))), None)

    def match_relationships(self, through_clause: int | None = None):
        return tuple((e, r) for e in self.matches
                     if through_clause is None or e.clause <= through_clause
                     for r in e.operator.relationships)
