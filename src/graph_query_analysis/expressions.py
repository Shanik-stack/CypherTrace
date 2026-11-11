"""To define constraines, arithmetic, aggregates and null expressions 
and how to proccess them, this will be used by transforms.py"""


"""Unevaluated, graph-independent expressions for symbolic Cypher analysis.

These are *mathematical representations*, not concrete rows.

Three independent levels are represented:

* ValueExpression / Predicate: values and conditions involving possible
  bindings (including the three-valued logic used by Cypher predicates).
  
* MultiplicityExpression: nonnegative counts of possible assignments,
  expressed symbolically using sums, products, indicators, and support.
  
* BagExpression: composable query-result operations, reusing the base class
  and UnitBag from model.py. Clause transformations will build these trees.

The next module (transforms.py) will translate the existing CypherAST JSON into
these types. A later interpreter/reasoner must define or prove their semantics;
creating expression objects alone does not establish any query findings.
"""

from dataclasses import dataclass
from typing import Literal

from graph_query_analysis.graph import NodeRequirement, RelationshipRequirement
from graph_query_analysis.model import (
    BagExpression,

    Symbol,
    ValueKind,
)


# ---------------------------------------------------------------------------
# Values: references to possible bindings, not values read from a database.
# ---------------------------------------------------------------------------

class ValueExpression:
    """Base type for a symbolic Cypher value (possibly null)."""


@dataclass(frozen=True)
class ValueLiteral(ValueExpression):
    """A literal occurring in the query; None denotes Cypher null."""

    value: str | int | float | bool | None


@dataclass(frozen=True)
class SymbolReference(ValueExpression):
    """A reference to a symbolic variable allocated by a clause transformer."""

    symbol: Symbol


@dataclass(frozen=True)
class ParameterReference(ValueExpression):
    """A Cypher parameter; its value may be unknown at analysis time."""

    name: str


@dataclass(frozen=True)
class PropertyAccess(ValueExpression):
    """Property lookup, e.g. PropertyAccess(SymbolReference(p), 'amount').

    Properties may be absent even when listed in an observed schema. A later
    semantic interpreter must account for null and Cypher's type rules.
    """

    owner: ValueExpression
    property_name: str


ArithmeticOperator = Literal["+", "-", "*", "/", "%"]


@dataclass(frozen=True)
class Arithmetic(ValueExpression):
    """Unevaluated scalar arithmetic; no Python arithmetic is performed."""

    operator: ArithmeticOperator
    left: ValueExpression
    right: ValueExpression


@dataclass(frozen=True)
class FunctionCall(ValueExpression):
    """An unevaluated scalar function, separate from aggregate functions."""

    name: str
    arguments: tuple[ValueExpression, ...] = ()


AggregateName = Literal["count", "sum", "avg", "min", "max", "collect"]


@dataclass(frozen=True)
class AggregateCall(ValueExpression):
    """Aggregate applied by an AggregateBag, not a normal scalar function.

    `argument=None` denotes COUNT(*) when name='count'. Other aggregates
    require an argument, which will be validated by the AST transformer.
    AVG/SUM/COUNT(expr) must handle nulls and input multiplicities. The
    result for global AVG with no non-null values is null, not zero.
    """

    name: AggregateName
    argument: ValueExpression | None = None
    distinct: bool = False


# ---------------------------------------------------------------------------
# Predicates: their eventual meaning is Cypher's TRUE / FALSE / NULL.
# A WHERE filter retains assignments only when its predicate is TRUE.
# ---------------------------------------------------------------------------

class Predicate:
    """Base type for a symbolic, potentially three-valued condition."""


@dataclass(frozen=True)
class TruthLiteral(Predicate):
    value: bool | None


ComparisonOperator = Literal["=", "<>", "<", "<=", ">", ">="]


@dataclass(frozen=True)
class Comparison(Predicate):
    operator: ComparisonOperator
    left: ValueExpression
    right: ValueExpression


@dataclass(frozen=True)
class StringPredicate(Predicate):
    """Case-sensitive Cypher string predicate with three-valued NULL behavior.

    Non-STRING or NULL operands yield NULL, which WHERE does not retain.
    """

    operator: Literal["STARTS WITH", "ENDS WITH", "CONTAINS"]
    left: ValueExpression
    right: ValueExpression


@dataclass(frozen=True)
class AllOf(Predicate):
    """Cypher AND: NULL is not the same as FALSE."""

    conditions: tuple[Predicate, ...]


@dataclass(frozen=True)
class AnyOf(Predicate):
    """Cypher OR: NULL is not the same as FALSE."""

    conditions: tuple[Predicate, ...]


@dataclass(frozen=True)
class Negation(Predicate):
    condition: Predicate


@dataclass(frozen=True)
class IsNull(Predicate):
    expression: ValueExpression
    negated: bool = False


@dataclass(frozen=True)
class HasLabel(Predicate):
    """A symbolic node has the requested label, not a lookup in NetworkX."""

    node: Symbol
    label: str


@dataclass(frozen=True)
class SameValue(Predicate):
    """Total equality for *row values*, including NULL = NULL for grouping.

    Unlike Cypher's comparison `=`, this returns TRUE/FALSE, never NULL.
    Node/relationship values compare by entity identity.
    """

    left: ValueExpression
    right: ValueExpression


@dataclass(frozen=True)
class NodeInGraph(Predicate):
    """The referenced value is a node of the underlying (unknown) data graph.

    Needed for node-only MATCH patterns without labels/relationships.
    """

    node: Symbol


Direction = Literal["OUTGOING", "INCOMING", "BOTH"]


@dataclass(frozen=True)
class RelationshipHolds(Predicate):
    """A possible relationship witness satisfies one pattern occurrence.

    left/right are textual positions in the Cypher pattern. `direction`
    determines actual endpoint orientation; BOTH admits either orientation
    but must not double-count a self-loop. Edge identities are symbolic.
    """

    relationship: Symbol
    left: Symbol
    right: Symbol
    types: tuple[str, ...] = ()
    direction: Direction = "OUTGOING"


# ---------------------------------------------------------------------------
# Multiplicity algebra. These expressions do not enumerate database rows.
# For any finite compatible graph, they denote nonnegative integer counts.
# ---------------------------------------------------------------------------

class MultiplicityExpression:
    """Base type for a symbolic bag multiplicity in the natural numbers."""


@dataclass(frozen=True)
class MultiplicityConstant(MultiplicityExpression):
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value < 0:
            raise ValueError("A bag multiplicity must be a nonnegative integer")


@dataclass(frozen=True)
class Indicator(MultiplicityExpression):
    """1 when the predicate is TRUE, 0 otherwise (including NULL)."""

    predicate: Predicate


@dataclass(frozen=True)
class Add(MultiplicityExpression):
    """Alternative assignments contribute additively. Empty sum means 0."""

    terms: tuple[MultiplicityExpression, ...]


@dataclass(frozen=True)
class Multiply(MultiplicityExpression):
    """Compatible assignment choices contribute multiplicatively.

    Compatibility of variable bindings/relationship identities must be
    encoded in the relevant predicates, not assumed from multiplication.
    Empty product means 1.
    """

    factors: tuple[MultiplicityExpression, ...]


@dataclass(frozen=True)
class SumOver(MultiplicityExpression):
    """Sum over possible symbolic assignments in an arbitrary finite graph.

    `bound` lists variables local to the sum. The semantic interpreter must
    choose their graph-dependent domains and obey scoping/identity constraints.
    This is NOT a Python iteration over known graph records.
    """

    bound: tuple[Symbol, ...]
    body: MultiplicityExpression


@dataclass(frozen=True)
class Support(MultiplicityExpression):
    """Cypher DISTINCT support: 1 if input multiplicity > 0, else 0."""

    source: MultiplicityExpression


# ---------------------------------------------------------------------------
# Bag-expression operations. These describe whole symbolic query results.
# The matching graph snapshot stays separately in SymbolicState.graph.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmptyBag(BagExpression):
    """No result assignments (contrast with UnitBag's one empty binding)."""


@dataclass(frozen=True)
class MatchBag(BagExpression):
    """Extend input bindings with a single MATCH clause's pattern witnesses.

    The requirements are only those introduced/tested by this clause, not the
    entire accumulated NetworkX graph. This distinction matters across WITH.
    The transformer reuses in-scope symbols and introduces fresh witnesses.
    Node labels, relationship types/endpoints, property-map and inline-pattern
    predicates are authoritative here, not in an additional FilterBag. Cypher
    25 requires different relationship occurrences within the same MATCH to
    bind to distinct relationship identities.
    """

    source: BagExpression
    nodes: tuple[NodeRequirement, ...] = ()
    relationships: tuple[RelationshipRequirement, ...] = ()


@dataclass(frozen=True)
class FilterBag(BagExpression):
    """Retain a binding exactly when its predicate is Cypher TRUE."""

    source: BagExpression
    predicate: Predicate


@dataclass(frozen=True)
class Projection:
    """Output column and its identity; type is never duplicated as a category."""

    alias: str | None
    expression: ValueExpression
    symbol: Symbol
    value_type: "CypherType | None" = None

    @property
    def value_kind(self) -> ValueKind:
        from graph_query_analysis.cypher_types import kind_of
        return kind_of(self.value_type) if self.value_type else "UNKNOWN"


@dataclass(frozen=True)
class ExtendBag(BagExpression):
    """Evaluate projections once while retaining the incoming bindings.

    Used by WITH ... WHERE when the WHERE predicate needs both newly computed
    aliases and input variables that WITH will later remove. ProjectBag then
    discards the hidden input bindings.
    """

    source: BagExpression
    columns: tuple[Projection, ...]


@dataclass(frozen=True)
class ProjectBag(BagExpression):
    """Project columns, summing multiplicities of equal resulting tuples.

    Bag interpretation sums multiplicities of equal output tuples. Under an
    ordered-stream interpretation, projection emits rows in input order.
    DISTINCT is a separate operation.
    """

    source: BagExpression
    columns: tuple[Projection, ...]


@dataclass(frozen=True)
class DistinctBag(BagExpression):
    """Apply support to each resulting row multiplicity after projection."""

    source: BagExpression


def aggregate_calls(expression: ValueExpression) -> tuple[AggregateCall, ...]:
    """Find aggregate occurrences recursively, without descending into their inputs."""
    if isinstance(expression, AggregateCall):
        return (expression,)
    if isinstance(expression, Arithmetic):
        return aggregate_calls(expression.left) + aggregate_calls(expression.right)
    if isinstance(expression, FunctionCall):
        return tuple(call for arg in expression.arguments for call in aggregate_calls(arg))
    if isinstance(expression, PropertyAccess):
        return aggregate_calls(expression.owner)
    return ()


def contains_aggregate(expression: ValueExpression) -> bool:
    return bool(aggregate_calls(expression))


@dataclass(frozen=True)
class GroupKey:
    """Expression evaluated on input rows to determine a grouping partition."""
    expression: ValueExpression
    symbol: Symbol


@dataclass(frozen=True)
class AggregateComputation:
    """One aggregate evaluated over the rows within a group."""
    expression: AggregateCall
    symbol: Symbol


@dataclass(frozen=True)
class AggregateBag(BagExpression):
    """Group input rows, compute aggregates, then evaluate ordered final columns.

    `columns` refer only to GroupKey and AggregateComputation output symbols.
    Grouping keys may be implicit or explicitly specified, including unprojected
    keys. No group keys means a single global group, even for empty input.
    """

    source: BagExpression
    grouping_keys: tuple[GroupKey, ...]
    aggregate_computations: tuple[AggregateComputation, ...]
    columns: tuple[Projection, ...]


@dataclass(frozen=True)
class SortKey:
    expression: ValueExpression
    descending: bool = False


@dataclass(frozen=True)
class SortBag(BagExpression):
    """Ordered row stream over a source bag, using Cypher ordering rules.

    A bag alone carries no row-order guarantee. Ties have unspecified ordering.
    An evaluator must represent this as a sequence, not only a row-count map.
    """
    source: BagExpression
    keys: tuple[SortKey, ...]


@dataclass(frozen=True)
class SliceBag(BagExpression):
    """SKIP/LIMIT on a row stream. Without SortBag, selected rows are arbitrary."""
    source: BagExpression
    skip: ValueExpression | None = None
    limit: ValueExpression | None = None
