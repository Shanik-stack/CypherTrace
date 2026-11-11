"""Schema-only symbolic bag semantics for the existing Cypher operator tree.
"""



from dataclasses import dataclass, fields, is_dataclass
from typing import Sequence

from graph_query_analysis.expressions import (
    Add, AggregateBag, AggregateCall, AllOf, AnyOf, Arithmetic, Comparison,
    DistinctBag, EmptyBag, ExtendBag, FilterBag, FunctionCall, GroupKey,
    HasLabel, Indicator, IsNull, MatchBag, MultiplicityConstant,
    MultiplicityExpression, Multiply, Negation, NodeInGraph, Predicate,
    ProjectBag, PropertyAccess, RelationshipHolds, SameValue, SliceBag,
    SortBag, SumOver, Support, SymbolReference, StringPredicate, TruthLiteral, ValueExpression,
    ValueLiteral,
)
from graph_query_analysis.model import BagExpression, Symbol, SymbolicState, UnitBag
from graph_query_analysis.cypher_types import FUNCTIONS


class SymbolicSemanticsUnsupported(NotImplementedError):
    """An operator requires semantics that an unordered symbolic bag cannot express."""


@dataclass(frozen=True)
class AggregateDenotation(ValueExpression):
    """Cypher aggregate value over a symbolically defined group.

    The input bag has multiplicity `source_multiplicity` for each possible
    `bound_inputs` assignment. Other source symbols (such as grouping
    node variables) remain free and identify the current group. Retain input
    assignments satisfying `group_predicate`.
    `call` is evaluated with those rows and multiplicities using Cypher's
    aggregate definition: count counts non-NULL entries (or all rows for *),
    sum/avg use non-NULL numeric entries and their multiplicities, min/max
    ignore NULL, and DISTINCT aggregates first deduplicate argument values.

    This is an *exact symbolic denotation*, not a numerically simplified result.
    In particular, equality involving this value cannot be decided from schema
    alone without a separate constraint solver or aggregate rewrite rules.
    """

    call: AggregateCall
    bound_inputs: tuple[Symbol, ...]
    source_multiplicity: MultiplicityExpression
    group_predicate: Predicate | None


@dataclass(frozen=True)
class RowCount(ValueExpression):
    """Integer-valued count obtained by summing input bag multiplicities.

    This bridges an existing multiplicity formula into an aggregate value;
    it does not introduce a second mathematical expression language.
    """

    amount: MultiplicityExpression


@dataclass(frozen=True)
class SymbolicBag:
    """A multiplicity formula whose free row variables are `symbols`.

    `ordering` is metadata. It does not turn bags into ordered sequences.
    `symbols` may include internal/anonymous witnesses before projection.
    """

    symbols: tuple[Symbol, ...]
    multiplicity: MultiplicityExpression
    ordering: str = "unspecified"


@dataclass(frozen=True)
class SymbolicStateResult:
    index: int
    clause: str
    scope: tuple[str, ...]
    row_symbols: tuple[str, ...]
    formula: MultiplicityExpression | None
    formula_text: str | None
    ordering: str
    limitation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "clause": self.clause,
            "scope": list(self.scope),
            "row_symbols": list(self.row_symbols),
            "multiplicity": self.formula_text,
            "ordering": self.ordering,
            "limitation": self.limitation,
            "requires_database_values": False,
        }


ZERO = MultiplicityConstant(0)
ONE = MultiplicityConstant(1)


def _unique(symbols: Sequence[Symbol]) -> tuple[Symbol, ...]:
    return tuple(dict.fromkeys(symbols))


def _times(*factors: MultiplicityExpression) -> MultiplicityExpression:
    flat: list[MultiplicityExpression] = []
    for factor in factors:
        if factor == ZERO:
            return ZERO
        if factor == ONE:
            continue
        if isinstance(factor, Multiply):
            flat.extend(factor.factors)
        else:
            flat.append(factor)
    return ONE if not flat else flat[0] if len(flat) == 1 else Multiply(tuple(flat))


def _sum(bound: Sequence[Symbol], body: MultiplicityExpression) -> MultiplicityExpression:
    if body == ZERO:
        return ZERO
    bound = _unique(bound)
    return SumOver(bound, body) if bound else body


def _condition(conditions: Sequence[Predicate]) -> MultiplicityExpression:
    return Indicator(conditions[0] if len(conditions) == 1 else AllOf(tuple(conditions))) if conditions else ONE


def _same(symbol: Symbol, value: ValueExpression) -> Predicate | None:
    if value == SymbolReference(symbol):
        return None
    return SameValue(SymbolReference(symbol), value)


def _symbol_rows(columns) -> tuple[Symbol, ...]:
    return _unique([column.symbol for column in columns])


def _projection_constraints(columns) -> MultiplicityExpression:
    equalities = [eq for column in columns
                  if (eq := _same(column.symbol, column.expression)) is not None]
    return _condition(equalities)



def _check_functions(tree: object) -> None:
    """Never claim exact multiplicities for volatile or unmodelled functions.

    This conservative guard applies to predicates as well as projections.
    Later work can extend the function contracts without changing bag algebra.
    """
    if isinstance(tree, FunctionCall):
        name = tree.name.lower()
        if name not in FUNCTIONS:
            raise SymbolicSemanticsUnsupported(
                f"Function {tree.name}() has no registered semantic contract"
            )
        if name == "rand":
            raise SymbolicSemanticsUnsupported(
                "rand() is nondeterministic per evaluation; a deterministic row-multiplicity "
                "formula requires a probabilistic/row-instance semantics"
            )
    if is_dataclass(tree):
        for field in fields(tree):
            _check_functions(getattr(tree, field.name))
    elif isinstance(tree, (tuple, list)):
        for child in tree:
            _check_functions(child)


def _pattern_conditions(op: MatchBag) -> tuple[Predicate, ...]:
    conditions: list[Predicate] = []
    for node in op.nodes:
        conditions.append(NodeInGraph(node.symbol))
        conditions.extend(HasLabel(node.symbol, label) for label in node.labels)
        conditions.extend(node.predicates)
    for rel in op.relationships:
        conditions.append(RelationshipHolds(rel.symbol, rel.left, rel.right,
                                            rel.types, rel.direction))
        conditions.extend(rel.predicates)
    # Neo4j 25: every two occurrences in one MATCH use different relationships.
    # This restriction is not imposed across separate MATCH clauses.
    for i, rel in enumerate(op.relationships):
        for later in op.relationships[i + 1:]:
            conditions.append(Comparison("<>", SymbolReference(rel.symbol),
                                         SymbolReference(later.symbol)))
    return tuple(conditions)


def _substitute_aggregates(expression: ValueExpression,
                           computations: dict[Symbol, ValueExpression]) -> ValueExpression:
    if isinstance(expression, SymbolReference):
        return computations.get(expression.symbol, expression)
    if isinstance(expression, Arithmetic):
        return Arithmetic(expression.operator,
                          _substitute_aggregates(expression.left, computations),
                          _substitute_aggregates(expression.right, computations))
    if isinstance(expression, PropertyAccess):
        return PropertyAccess(_substitute_aggregates(expression.owner, computations),
                              expression.property_name)
    if isinstance(expression, FunctionCall):
        return FunctionCall(expression.name, tuple(
            _substitute_aggregates(arg, computations) for arg in expression.arguments))
    return expression


def symbolic_bag(bag: BagExpression) -> SymbolicBag:
    """Interpret the compiled operator tree without a database.

    Project uses total row-value equality, not Cypher three-valued `=`.
    """
    return _symbolic_bag(bag, {})


def _symbolic_bag(bag: BagExpression, cache: dict[int, SymbolicBag]) -> SymbolicBag:
    """Share previous operator results by identity, without changing their classes."""
    key = id(bag)
    if key not in cache:
        cache[key] = _symbolic_bag_uncached(bag, cache)
    return cache[key]


def _symbolic_bag_uncached(bag: BagExpression, cache: dict[int, SymbolicBag]) -> SymbolicBag:
    """One operator's mathematical interpretation, built from its input."""
    if isinstance(bag, UnitBag):
        return SymbolicBag((), ONE)
    if isinstance(bag, EmptyBag):
        return SymbolicBag((), ZERO)
    if isinstance(bag, MatchBag):
        previous = _symbolic_bag(bag.source, cache)
        symbols = _unique((*previous.symbols,
                           *(node.symbol for node in bag.nodes),
                           *(rel.left for rel in bag.relationships),
                           *(rel.right for rel in bag.relationships),
                           *(rel.symbol for rel in bag.relationships)))
        conditions = _pattern_conditions(bag)
        _check_functions(conditions)
        return SymbolicBag(symbols, _times(previous.multiplicity, _condition(conditions)))
    if isinstance(bag, FilterBag):
        previous = _symbolic_bag(bag.source, cache)
        _check_functions(bag.predicate)
        return SymbolicBag(previous.symbols,
                           _times(previous.multiplicity, Indicator(bag.predicate)),
                           previous.ordering)
    if isinstance(bag, ExtendBag):
        previous = _symbolic_bag(bag.source, cache)
        _check_functions(bag.columns)
        return SymbolicBag(_unique((*previous.symbols, *_symbol_rows(bag.columns))),
                           _times(previous.multiplicity, _projection_constraints(bag.columns)),
                           previous.ordering)
    if isinstance(bag, ProjectBag):
        previous = _symbolic_bag(bag.source, cache)
        _check_functions(bag.columns)
        result = _symbol_rows(bag.columns)
        dropped = tuple(s for s in previous.symbols if s not in result)
        body = _times(previous.multiplicity, _projection_constraints(bag.columns))
        return SymbolicBag(result, _sum(dropped, body), previous.ordering)
    if isinstance(bag, DistinctBag):
        previous = _symbolic_bag(bag.source, cache)
        return SymbolicBag(previous.symbols, Support(previous.multiplicity))
    if isinstance(bag, SortBag):
        previous = _symbolic_bag(bag.source, cache)
        _check_functions(bag.keys)
        return SymbolicBag(previous.symbols, previous.multiplicity,
                           "ordered by specified keys; ties unspecified")
    if isinstance(bag, SliceBag):
        raise SymbolicSemanticsUnsupported(
            "SKIP/LIMIT selects occurrences from a row stream; an unordered bag "
            "formula alone cannot determine which rows survive, especially with ties"
        )
    if isinstance(bag, AggregateBag):
        previous = _symbolic_bag(bag.source, cache)
        _check_functions(bag.grouping_keys)
        _check_functions(bag.aggregate_computations)
        _check_functions(bag.columns)
        group_symbols = _unique([key.symbol for key in bag.grouping_keys])
        group_constraints = _condition([eq for key in bag.grouping_keys
                                        if (eq := _same(key.symbol, key.expression)) is not None])
        group_predicate = (None if group_constraints == ONE else
                           group_constraints.predicate if isinstance(group_constraints, Indicator) else None)
        group_weight = _sum([s for s in previous.symbols if s not in group_symbols],
                            _times(previous.multiplicity, group_constraints))
        # Global aggregation always has one group, including on empty input.
        exists = ONE if not bag.grouping_keys else Support(group_weight)

        bound_inputs = tuple(s for s in previous.symbols if s not in group_symbols)
        computations: dict[Symbol, ValueExpression] = {}
        for comp in bag.aggregate_computations:
            call = comp.expression
            if call.name == "count" and not call.distinct:
                weighted_rows = _times(
                    previous.multiplicity, group_constraints,
                    Indicator(IsNull(call.argument, negated=True))
                    if call.argument is not None else ONE,
                )
                computations[comp.symbol] = RowCount(_sum(bound_inputs, weighted_rows))
            else:
                computations[comp.symbol] = AggregateDenotation(
                    call, bound_inputs, previous.multiplicity, group_predicate
                )
        for comp in bag.aggregate_computations:
            if comp.expression.name == "collect":
                raise SymbolicSemanticsUnsupported(
                    "collect() produces a list whose order cannot be inferred from bag multiplicities; "
                    "ordered collection semantics must be interpreted separately"
                )
        columns = [type(col)(col.alias,
                             _substitute_aggregates(col.expression, computations),
                             col.symbol, col.value_type)
                   for col in bag.columns]
        result = _symbol_rows(columns)
        # Group keys not projected must still range over possible groups.
        missing_keys = tuple(s for s in group_symbols if s not in result)
        return SymbolicBag(result,
                           _sum(missing_keys,
                                _times(exists, _projection_constraints(columns))))
    raise SymbolicSemanticsUnsupported(f"No symbolic bag rule for {type(bag).__name__}")


def multiplicity(bag: BagExpression) -> MultiplicityExpression:
    """Convenience: multiplicity formula with free variables named by symbolic_bag."""
    return symbolic_bag(bag).multiplicity


def _name(symbol: Symbol) -> str:
    return symbol.name.replace("\x00", "$")


def format_value(value: ValueExpression) -> str:
    if isinstance(value, SymbolReference):
        return _name(value.symbol)
    if isinstance(value, ValueLiteral):
        return repr(value.value)
    if isinstance(value, Arithmetic):
        return f"({format_value(value.left)} {value.operator} {format_value(value.right)})"
    if isinstance(value, PropertyAccess):
        return f"{format_value(value.owner)}.{value.property_name}"
    if isinstance(value, FunctionCall):
        return f"{value.name}({', '.join(format_value(x) for x in value.arguments)})"
    if isinstance(value, RowCount):
        return f"COUNT_ROWS({format_multiplicity(value.amount)})"
    if isinstance(value, AggregateDenotation):
        return f"{value.call.name.upper()}[group]({format_value(value.call.argument) if value.call.argument else '*'})"
    return repr(value)


def format_predicate(predicate: Predicate) -> str:
    if isinstance(predicate, SameValue):
        return f"same({format_value(predicate.left)}, {format_value(predicate.right)})"
    if isinstance(predicate, NodeInGraph):
        return f"node_in_graph({_name(predicate.node)})"
    if isinstance(predicate, HasLabel):
        return f"{_name(predicate.node)}:{predicate.label}"
    if isinstance(predicate, RelationshipHolds):
        return f"edge({_name(predicate.relationship)}, {_name(predicate.left)}, {_name(predicate.right)}, {predicate.types}, {predicate.direction})"
    if isinstance(predicate, Comparison):
        return f"{format_value(predicate.left)} {predicate.operator} {format_value(predicate.right)}"
    if isinstance(predicate, StringPredicate):
        return f"{format_value(predicate.left)} {predicate.operator} {format_value(predicate.right)}"
    if isinstance(predicate, IsNull):
        return f"{format_value(predicate.expression)} IS {'NOT ' if predicate.negated else ''}NULL"
    if isinstance(predicate, TruthLiteral):
        return str(predicate.value).upper()
    if isinstance(predicate, AllOf):
        return "(" + " AND ".join(format_predicate(p) for p in predicate.conditions) + ")"
    if isinstance(predicate, AnyOf):
        return "(" + " OR ".join(format_predicate(p) for p in predicate.conditions) + ")"
    if isinstance(predicate, Negation):
        return f"NOT ({format_predicate(predicate.condition)})"
    return repr(predicate)


def format_multiplicity(expression: MultiplicityExpression) -> str:
    if isinstance(expression, MultiplicityConstant):
        return str(expression.value)
    if isinstance(expression, Indicator):
        return "[" + format_predicate(expression.predicate) + "]"
    if isinstance(expression, Multiply):
        return " * ".join(format_multiplicity(x) for x in expression.factors)
    if isinstance(expression, Add):
        return " + ".join(format_multiplicity(x) for x in expression.terms)
    if isinstance(expression, SumOver):
        return f"SUM_{{{', '.join(_name(s) for s in expression.bound)}}}({format_multiplicity(expression.body)})"
    if isinstance(expression, Support):
        return f"support({format_multiplicity(expression.source)})"
    return repr(expression)


def symbolic_trace(states: Sequence[SymbolicState],
                   clauses: Sequence[dict] | None = None,
                   *, format_formulas: bool = True) -> tuple[SymbolicStateResult, ...]:
    """One LLM-readable, schema-only summary per compiler state.

    Unsupported semantics are reported explicitly; previous valid states survive.
    This makes it safe to use for feedback without inventing database rows.
    """
    results = []
    cache: dict[int, SymbolicBag] = {}
    for index, state in enumerate(states):
        clause = ("INITIAL" if index == 0 else clauses[index - 1]["type"]
                  if clauses is not None else type(state.bag).__name__)
        try:
            result = _symbolic_bag(state.bag, cache)
            results.append(SymbolicStateResult(
                index, clause, tuple(b.variable for b in state.scope),
                tuple(_name(s) for s in result.symbols), result.multiplicity,
                (format_multiplicity(result.multiplicity) if format_formulas else None),
                result.ordering))
        except SymbolicSemanticsUnsupported as exc:
            results.append(SymbolicStateResult(
                index, clause, tuple(b.variable for b in state.scope),
                (), None, None, "unknown", str(exc)))
    return tuple(results)
