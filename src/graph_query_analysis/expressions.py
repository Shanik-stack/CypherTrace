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
    NodeSymbol,
    RelationshipSymbol,
    Symbol,
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

    node: NodeSymbol
    label: str


Direction = Literal["OUTGOING", "INCOMING", "BOTH"]


@dataclass(frozen=True)
class RelationshipHolds(Predicate):
    """A possible relationship witness satisfies one pattern occurrence.

    left/right are textual positions in the Cypher pattern. `direction`
    determines actual endpoint orientation; BOTH admits either orientation
    but must not double-count a self-loop. Edge identities are symbolic.
    """

    relationship: RelationshipSymbol
    left: NodeSymbol
    right: NodeSymbol
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
    The transformer must reuse symbols for bound variables and generate fresh
    symbols for anonymous nodes/relationships. Relationship uniqueness and
    multi-path matching semantics will be imposed by the transformer.
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
    """One output column: evaluate an expression and expose it as alias."""

    alias: str
    expression: ValueExpression


@dataclass(frozen=True)
class ProjectBag(BagExpression):
    """Project columns, summing multiplicities of equal resulting tuples.

    Projection does not remove duplicates. DISTINCT must wrap ProjectBag.
    """

    source: BagExpression
    columns: tuple[Projection, ...]


@dataclass(frozen=True)
class DistinctBag(BagExpression):
    """Apply support to each resulting row multiplicity after projection."""

    source: BagExpression


@dataclass(frozen=True)
class AggregateBag(BagExpression):
    """Group input bindings and compute aggregate projections.

    When `grouping=()`, this is a global aggregation producing one result row
    even when source is empty (e.g. AVG then yields null). AggregateCall
    semantics, including DISTINCT arguments and nulls, are defined later.
    """

    source: BagExpression
    grouping: tuple[Projection, ...] = ()
    aggregates: tuple[Projection, ...] = ()
