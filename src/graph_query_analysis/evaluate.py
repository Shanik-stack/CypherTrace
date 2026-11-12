"""Finite-graph interpreter for the supported read-only Cypher 25 bag fragment.

This evaluates the *same* expression tree produced by transforms.py. Each row
is an environment from symbolic identifiers to concrete values; duplicates in
`rows` are intentional (a literal bag represented as repeated rows).
`ordered=True` denotes an explicit ORDER BY stream. On unordered input,
iteration order is only a sample and must not be interpreted as a guarantee.
"""


from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Mapping
import random
import math

from graph_query_analysis.expressions import (
    AggregateBag, AggregateCall, AllOf, AnyOf, Arithmetic, Comparison, DistinctBag,
    EmptyBag, ExtendBag, FilterBag, FunctionCall, HasLabel, IsNull, MatchBag,
    Negation, ParameterReference, Predicate, ProjectBag, PropertyAccess,
    RelationshipHolds, SliceBag, SortBag, SymbolReference, StringPredicate, TruthLiteral,
    ValueExpression, ValueLiteral,
)
from graph_query_analysis.model import BagExpression, Symbol, SymbolicState, UnitBag
from graph_query_analysis.cypher_types import call_function


class EvaluationError(ValueError):
    """Cannot faithfully execute the requested fragment on the supplied graph."""


@dataclass(frozen=True, eq=False)
class NodeRecord:
    id: str | int
    labels: frozenset[str]
    properties: Mapping[str, object] = field(default_factory=dict, compare=False)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NodeRecord) and self.id == other.id

    def __hash__(self) -> int:
        return hash((NodeRecord, self.id))


@dataclass(frozen=True, eq=False)
class RelationshipRecord:
    id: str | int
    start: str | int
    end: str | int
    type: str
    properties: Mapping[str, object] = field(default_factory=dict, compare=False)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RelationshipRecord) and self.id == other.id

    def __hash__(self) -> int:
        return hash((RelationshipRecord, self.id))


@dataclass(frozen=True)
class PropertyGraph:
    nodes: tuple[NodeRecord, ...]
    relationships: tuple[RelationshipRecord, ...] = ()

    def __post_init__(self) -> None:
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise EvaluationError("Duplicate node identity")
        if len({r.id for r in self.relationships}) != len(self.relationships):
            raise EvaluationError("Duplicate relationship identity")
        for rel in self.relationships:
            if rel.start not in node_ids or rel.end not in node_ids:
                raise EvaluationError(f"Relationship {rel.id!r} has an unknown endpoint")


@dataclass(frozen=True)
class EvaluatedRows:
    rows: tuple[dict[Symbol, object], ...]
    ordered: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class StateResult:
    """User-facing intermediate rows, with duplicate rows retained."""
    index: int
    clause: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    ordered: bool
    types: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, object]:
        """JSON-compatible view for an LLM's intermediate-state feedback."""
        return {
            "index": self.index, "clause": self.clause,
            "columns": list(self.columns), "types": list(self.types), "row_count": self.count,
            "ordered": self.ordered,
            "rows": [[_public_value(v) for v in row] for row in self.rows],
            "multiplicities": [
                {"row": [_public_value(v) for v in row], "count": count}
                for row, count in self.multiplicities()
            ],
        }

    def multiplicities(self) -> tuple[tuple[tuple[object, ...], int], ...]:
        counts: dict[object, int] = {}
        first: dict[object, tuple[object, ...]] = {}
        for row in self.rows:
            key = _freeze(row)
            counts[key] = counts.get(key, 0) + 1
            first.setdefault(key, row)
        return tuple((first[k], n) for k, n in counts.items())


def _public_value(value: object) -> object:
    if isinstance(value, NodeRecord):
        return {"kind": "node", "id": value.id, "labels": sorted(value.labels),
                "properties": {k: _public_value(v) for k, v in value.properties.items()}}
    if isinstance(value, RelationshipRecord):
        return {"kind": "relationship", "id": value.id, "type": value.type,
                "start": value.start, "end": value.end,
                "properties": {k: _public_value(v) for k, v in value.properties.items()}}
    if isinstance(value, (list, tuple)):
        return [_public_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _public_value(v) for k, v in value.items()}
    return value


def _freeze(value: object) -> object:
    if isinstance(value, NodeRecord):
        return ("node", value.id)
    if isinstance(value, RelationshipRecord):
        return ("relationship", value.id)
    if isinstance(value, (list, tuple)):
        return ("list", tuple(_freeze(v) for v in value))
    if isinstance(value, dict):
        return ("map", tuple(sorted((str(k), _freeze(v)) for k, v in value.items())))
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    return (type(value).__name__, value)


def _equal(left, right):
    """Predicate equality is nullable, unlike the total equality used by bags."""
    if left is None or right is None:
        return None
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        comparisons = [_equal(a, b) for a, b in zip(left, right)]
    elif isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        comparisons = [_equal(left[k], right[k]) for k in left]
    else:
        if any(isinstance(v, float) and math.isnan(v) for v in (left, right)):
            return False
        return _freeze(left) == _freeze(right)
    if False in comparisons:
        return False
    return None if None in comparisons else True


class BagEvaluator:
    """Evaluate immutable bag expressions over one finite property graph.

    An evaluator caches operators, so inspecting intermediate states does not
    re-run nondeterministic expressions in shared upstream operations.
    """
    def __init__(self, graph: PropertyGraph, parameters: Mapping[str, object] | None = None,
                 *, seed: int = 0):
        self.graph = graph
        self.parameters = dict(parameters or {})
        self.rng = random.Random(seed)
        self._cache: dict[int, EvaluatedRows] = {}

    def value(self, expression: ValueExpression, row: Mapping[Symbol, object]) -> object:
        if isinstance(expression, ValueLiteral):
            return expression.value
        if isinstance(expression, SymbolReference):
            try:
                return row[expression.symbol]
            except KeyError as exc:
                raise EvaluationError(f"Unbound symbol: {expression.symbol.name!r}") from exc
        if isinstance(expression, ParameterReference):
            if expression.name not in self.parameters:
                raise EvaluationError(f"Missing parameter ${expression.name}")
            return self.parameters[expression.name]
        if isinstance(expression, PropertyAccess):
            owner = self.value(expression.owner, row)
            if owner is None:
                return None
            if isinstance(owner, (NodeRecord, RelationshipRecord)):
                return owner.properties.get(expression.property_name)
            if isinstance(owner, dict):
                return owner.get(expression.property_name)
            raise EvaluationError(f"Cannot access property {expression.property_name} on {type(owner).__name__}")
        if isinstance(expression, Arithmetic):
            a, b = self.value(expression.left, row), self.value(expression.right, row)
            if a is None or b is None:
                return None
            if isinstance(a, bool) or isinstance(b, bool):
                raise EvaluationError("Boolean operands are not numeric")
            if expression.operator == '+' and isinstance(a, str) and isinstance(b, str):
                return a + b
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                raise EvaluationError('Numeric arithmetic requires numbers')
            try:
                if expression.operator == "+": return a + b
                if expression.operator == "-": return a - b
                if expression.operator == "*": return a * b
                if expression.operator in ('/', '%'):
                    if type(a) is int and type(b) is int:
                        # Cypher integer division truncates toward zero, without float rounding.
                        quotient = (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
                        return quotient if expression.operator == '/' else a - quotient * b
                    return a / b if expression.operator == '/' else math.fmod(a, b)
            except (ValueError, TypeError, ZeroDivisionError) as exc:
                raise EvaluationError(f"Invalid arithmetic {expression.operator}: {exc}") from exc
        if isinstance(expression, FunctionCall):
            # COALESCE short-circuits, so unevaluated rand()/errors in later
            # alternatives cannot affect a known non-null first choice.
            if expression.name.lower() == "coalesce":
                for arg in expression.arguments:
                    result = self.value(arg, row)
                    if result is not None:
                        return result
                return None
            args = tuple(self.value(arg, row) for arg in expression.arguments)
            try:
                return call_function(expression.name, args, self.rng)
            except (ValueError, TypeError, IndexError) as exc:
                raise EvaluationError(f"Invalid {expression.name}() call: {exc}") from exc
        if isinstance(expression, AggregateCall):
            raise EvaluationError("AggregateCall must be evaluated by AggregateBag")
        raise EvaluationError(f"Unsupported value expression: {type(expression).__name__}")

    def predicate(self, pred: Predicate, row: Mapping[Symbol, object]) -> bool | None:
        if isinstance(pred, TruthLiteral): return pred.value
        if isinstance(pred, Comparison):
            a, b = self.value(pred.left, row), self.value(pred.right, row)
            if a is None or b is None:
                return None
            try:
                if pred.operator in ('=', '<>'):
                    equal = _equal(a, b)
                    return equal if pred.operator == '=' or equal is None else not equal
                if pred.operator == "<": return a < b
                if pred.operator == "<=": return a <= b
                if pred.operator == ">": return a > b
                if pred.operator == ">=": return a >= b
            except TypeError as exc:
                raise EvaluationError(f"Invalid comparison {pred.operator}: {exc}") from exc
        if isinstance(pred, StringPredicate):
            left, right = self.value(pred.left, row), self.value(pred.right, row)
            if not isinstance(left, str) or not isinstance(right, str):
                return None
            if pred.operator == "STARTS WITH":
                return left.startswith(right)
            if pred.operator == "ENDS WITH":
                return left.endswith(right)
            if pred.operator == "CONTAINS":
                return right in left
            raise EvaluationError(f"Unsupported string predicate {pred.operator!r}")
        if isinstance(pred, AllOf):
            saw_null = False
            for part in pred.conditions:
                result = self.predicate(part, row)
                if result is False:
                    return False
                if result is None:
                    saw_null = True
            return None if saw_null else True
        if isinstance(pred, AnyOf):
            saw_null = False
            for part in pred.conditions:
                result = self.predicate(part, row)
                if result is True:
                    return True
                if result is None:
                    saw_null = True
            return None if saw_null else False
        if isinstance(pred, Negation):
            v = self.predicate(pred.condition, row)
            return None if v is None else not v
        if isinstance(pred, IsNull):
            v = self.value(pred.expression, row) is None
            return not v if pred.negated else v
        if isinstance(pred, HasLabel):
            n = row.get(pred.node)
            return pred.label in n.labels if isinstance(n, NodeRecord) else None
        if isinstance(pred, RelationshipHolds):
            rel = row.get(pred.relationship)
            left, right = row.get(pred.left), row.get(pred.right)
            if not isinstance(rel, RelationshipRecord) or not isinstance(left, NodeRecord) or not isinstance(right, NodeRecord):
                return None
            return self._rel_holds(rel, left, right, pred.types, pred.direction)
        raise EvaluationError(f"Unsupported predicate: {type(pred).__name__}")

    @staticmethod
    def _rel_holds(rel: RelationshipRecord, left: NodeRecord, right: NodeRecord,
                   types: tuple[str, ...], direction: str) -> bool:
        if types and rel.type not in types:
            return False
        forward = rel.start == left.id and rel.end == right.id
        backward = rel.start == right.id and rel.end == left.id
        return forward if direction == "OUTGOING" else backward if direction == "INCOMING" else forward or backward

    def _match(self, op: MatchBag) -> EvaluatedRows:
        source = self.evaluate(op.source)
        results: list[dict[Symbol, object]] = []
        for input_row in source.rows:
            def bind_nodes(index: int, row: dict[Symbol, object]) -> None:
                if index == len(op.nodes):
                    bind_rels(0, row, set())
                    return
                req = op.nodes[index]
                if req.symbol in row:
                    options = [row[req.symbol]]
                else:
                    options = self.graph.nodes
                for node in options:
                    if not isinstance(node, NodeRecord) or not set(req.labels).issubset(node.labels):
                        continue
                    new_row = {**row, req.symbol: node}
                    bind_nodes(index + 1, new_row)

            def bind_rels(index: int, row: dict[Symbol, object], used: set[object]) -> None:
                if index == len(op.relationships):
                    # Pattern property maps and inline pattern predicates are
                    # authoritative here; clause-level WHERE is FilterBag.
                    if all(self.predicate(p, row) is True for n in op.nodes for p in n.predicates) and all(
                        self.predicate(p, row) is True for r in op.relationships for p in r.predicates):
                        results.append(row)
                    return
                req = op.relationships[index]
                left, right = row[req.left], row[req.right]
                options = [row[req.symbol]] if req.symbol in row else self.graph.relationships
                for rel in options:
                    if (not isinstance(rel, RelationshipRecord) or rel.id in used
                            or not self._rel_holds(rel, left, right, req.types, req.direction)):
                        continue
                    bind_rels(index + 1, {**row, req.symbol: rel}, used | {rel.id})

            bind_nodes(0, dict(input_row))
        return EvaluatedRows(tuple(results), source.ordered)

    def _aggregate(self, op: AggregateBag) -> EvaluatedRows:
        source = self.evaluate(op.source)
        groups: dict[object, tuple[tuple[object, ...], list[dict[Symbol, object]]]] = {}
        for row in source.rows:
            values = tuple(self.value(k.expression, row) for k in op.grouping_keys)
            key = _freeze(values)
            if key not in groups:
                groups[key] = (values, [])
            groups[key][1].append(row)
        if not op.grouping_keys and not groups:
            groups[_freeze(())] = ((), [])
        result = []
        for values, rows in groups.values():
            group_row: dict[Symbol, object] = {k.symbol: v for k, v in zip(op.grouping_keys, values)}
            for aggregate in op.aggregate_computations:
                call = aggregate.expression
                args = ([self.value(call.argument, row) for row in rows]
                        if call.argument is not None else [1] * len(rows))
                args = [a for a in args if a is not None]
                if call.distinct:
                    dedup = set()
                    distinct_args = []
                    for a in args:
                        f = _freeze(a)
                        if f not in dedup:
                            distinct_args.append(a)
                            dedup.add(f)
                    args = distinct_args
                if call.name in ('sum', 'avg') and any(type(a) not in (int, float) for a in args):
                    raise EvaluationError(f'{call.name} requires numeric values in this evaluator')
                if call.name == "count": value = len(args)
                elif call.name == "sum": value = sum(args)
                elif call.name == "avg": value = sum(args) / len(args) if args else None
                elif call.name == "min": value = min(args) if args else None
                elif call.name == "max": value = max(args) if args else None
                elif call.name == "collect": value = list(args)
                else: raise EvaluationError(f"Unknown aggregate {call.name}")
                group_row[aggregate.symbol] = value
            output = {c.symbol: self.value(c.expression, group_row) for c in op.columns}
            result.append(output)
        return EvaluatedRows(tuple(result), False)

    def evaluate(self, bag: BagExpression) -> EvaluatedRows:
        key = id(bag)
        if key in self._cache:
            return self._cache[key]
        if isinstance(bag, UnitBag):
            result = EvaluatedRows(({},), False)
        elif isinstance(bag, EmptyBag):
            result = EvaluatedRows((), False)
        elif isinstance(bag, MatchBag):
            result = self._match(bag)
        elif isinstance(bag, FilterBag):
            source = self.evaluate(bag.source)
            result = EvaluatedRows(tuple(row for row in source.rows if self.predicate(bag.predicate, row) is True), source.ordered)
        elif isinstance(bag, ExtendBag):
            source = self.evaluate(bag.source)
            out = []
            for row in source.rows:
                # Evaluate every projection using input row values, then extend.
                values = [(c.symbol, self.value(c.expression, row)) for c in bag.columns]
                combined = dict(row)
                combined.update(values)
                out.append(combined)
            result = EvaluatedRows(tuple(out), source.ordered)
        elif isinstance(bag, ProjectBag):
            source = self.evaluate(bag.source)
            result = EvaluatedRows(tuple({c.symbol: self.value(c.expression, row) for c in bag.columns}
                                         for row in source.rows), source.ordered)
        elif isinstance(bag, DistinctBag):
            source = self.evaluate(bag.source)
            seen = set()
            selected = []
            for row in source.rows:
                signature = _freeze(tuple(sorted(((type(s).__name__, s.name, v) for s, v in row.items()), key=lambda x: (x[0], x[1]))))
                if signature not in seen:
                    selected.append(row)
                    seen.add(signature)
            result = EvaluatedRows(tuple(selected), source.ordered)
        elif isinstance(bag, AggregateBag):
            result = self._aggregate(bag)
        elif isinstance(bag, SortBag):
            source = self.evaluate(bag.source)
            with_keys = [(row, tuple(self.value(k.expression, row) for k in bag.keys)) for row in source.rows]
            def compare(a, b):
                for i, key in enumerate(bag.keys):
                    v1, v2 = a[1][i], b[1][i]
                    if v1 is None or v2 is None:
                        cmp = (v1 is None) - (v2 is None)  # ascending null last
                    else:
                        try:
                            cmp = (v1 > v2) - (v1 < v2)
                        except TypeError as exc:
                            raise EvaluationError(f"Unsupported sort values: {v1!r} and {v2!r}") from exc
                    if cmp:
                        return -cmp if key.descending else cmp
                return 0
            result = EvaluatedRows(tuple(row for row, _ in sorted(with_keys, key=cmp_to_key(compare))), True)
        elif isinstance(bag, SliceBag):
            source = self.evaluate(bag.source)
            skip = self.value(bag.skip, {}) if bag.skip is not None else 0
            limit = self.value(bag.limit, {}) if bag.limit is not None else None
            if (type(skip) is not int or skip < 0 or
                (limit is not None and (type(limit) is not int or limit < 0))):
                raise EvaluationError("SKIP/LIMIT must be nonnegative integers")
            result = EvaluatedRows(source.rows[skip:skip+limit if limit is not None else None], source.ordered)
        else:
            raise EvaluationError(f"No interpreter for bag operator {type(bag).__name__}")
        self._cache[key] = result
        return result

    def trace(self, states: tuple[SymbolicState, ...], *,
              clause_names: tuple[str, ...] = ()) -> tuple[StateResult, ...]:
        """Return one visible result per state; duplicates are retained."""
        results = []
        for i, state in enumerate(states):
            evaluated = self.evaluate(state.bag)
            columns = tuple(b.variable for b in state.scope)
            values = tuple(tuple(row.get(b.symbol) for b in state.scope) for row in evaluated.rows)
            types = tuple(str(b.value_type) if b.value_type is not None else b.value_kind for b in state.scope)
            if i == len(states) - 1:
                op = state.bag
                while hasattr(op, "source") and not isinstance(op, (ProjectBag, AggregateBag)):
                    op = op.source
                if isinstance(op, (ProjectBag, AggregateBag)):
                    columns = tuple(c.alias or f"<position {j}>" for j, c in enumerate(op.columns))
                    values = tuple(tuple(row.get(c.symbol) for c in op.columns) for row in evaluated.rows)
                    types = tuple(str(c.value_type) if c.value_type is not None else c.value_kind for c in op.columns)
            clause = "INITIAL" if i == 0 else (clause_names[i - 1] if i-1 < len(clause_names) else "CLAUSE")
            results.append(StateResult(i, clause, columns, values, evaluated.ordered, types))
        return tuple(results)


def evaluate_request(request, graph: PropertyGraph, *, seed: int = 0) -> tuple[StateResult, ...]:
    """Convenience entry point: compile a CypherAST request and inspect each clause."""
    from graph_query_analysis.compiler import compile_request
    states = compile_request(request)
    return BagEvaluator(graph, request.parameters, seed=seed).trace(
        states, clause_names=tuple(c["type"].upper() for c in request.clauses))
