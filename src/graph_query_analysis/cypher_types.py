"""Cypher 25 expression types and a small, shared function contract registry.

This is *static* analysis. UNKNOWN means insufficient schema/signature information;
it never licenses treating a computed value as a node. Runtime functions below
cover only the registered fragment, rather than guessing unknown functions.
"""


from dataclasses import dataclass
from typing import Callable
import random

from graph_query_analysis.expressions import (AggregateCall, Arithmetic, FunctionCall, ParameterReference,
                          PropertyAccess, SymbolReference, ValueExpression, ValueLiteral)
from graph_query_analysis.model import NodeSymbol, RelationshipSymbol, Symbol, SymbolicState


class CypherTypeError(ValueError):
    pass


class UnsupportedFunctionError(NotImplementedError):
    pass


@dataclass(frozen=True)
class CypherType:
    name: str
    element: "CypherType | None" = None
    nullable: bool = False

    def optional(self) -> "CypherType":
        return CypherType(self.name, self.element, True)

    def __str__(self) -> str:
        name = f"LIST<{self.element}>" if self.name == "LIST" and self.element else self.name
        return name + (" | NULL" if self.nullable else "")


UNKNOWN = CypherType("UNKNOWN", nullable=True)
NULL = CypherType("NULL", nullable=True)
NODE = CypherType("NODE")
RELATIONSHIP = CypherType("RELATIONSHIP")
INTEGER = CypherType("INTEGER")
FLOAT = CypherType("FLOAT")
NUMBER = CypherType("NUMBER")
STRING = CypherType("STRING")
BOOLEAN = CypherType("BOOLEAN")


def LIST(element: CypherType = UNKNOWN) -> CypherType:
    return CypherType("LIST", element)


def join_types(*types: CypherType) -> CypherType:
    concrete = [t for t in types if t.name != "NULL"]
    if not concrete:
        return NULL
    nullable = any(t.nullable or t.name == "NULL" for t in types)
    if any(t.name == "UNKNOWN" for t in concrete):
        return CypherType("UNKNOWN", nullable=True)
    names = {t.name for t in concrete}
    if names <= {"INTEGER", "FLOAT", "NUMBER"}:
        name = "NUMBER" if len(names) > 1 else concrete[0].name
        return CypherType(name, nullable=nullable)
    if names == {"LIST"}:
        return CypherType("LIST", join_types(*(t.element or UNKNOWN for t in concrete)), nullable)
    if len(names) == 1:
        return CypherType(concrete[0].name, concrete[0].element, nullable)
    return CypherType("UNKNOWN", nullable=True)


def from_schema(type_name: str | None) -> CypherType:
    if not type_name:
        return UNKNOWN
    text = type_name.upper().strip()
    if text.startswith("LIST<") and text.endswith(">"):
        return LIST(from_schema(text[5:-1])).optional()
    mapping = {"INT": INTEGER, "INTEGER": INTEGER, "LONG": INTEGER,
               "DOUBLE": FLOAT, "FLOAT": FLOAT, "STRING": STRING, "BOOLEAN": BOOLEAN,
               "BOOL": BOOLEAN, "NODE": NODE, "RELATIONSHIP": RELATIONSHIP}
    return mapping.get(text, UNKNOWN).optional()


def kind_of(t: CypherType) -> str:
    return "NODE" if t.name == "NODE" else "RELATIONSHIP" if t.name == "RELATIONSHIP" else (
        "UNKNOWN" if t.name == "UNKNOWN" else "SCALAR")


def _require_arity(name: str, args: tuple[CypherType, ...], low: int, high: int | None = None) -> None:
    if len(args) < low or (high is not None and len(args) > high):
        raise CypherTypeError(f"{name}() expects {low}" + (f"..{high}" if high != low else "") + " argument(s)")


def _require_family(name: str, t: CypherType, expected: set[str]) -> None:
    if t.name not in expected | {"UNKNOWN", "NULL"}:
        raise CypherTypeError(f"{name}() expects {', '.join(sorted(expected))}, got {t}")


def _infer_coalesce(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("coalesce", args, 1)
    combined = join_types(*args)
    # coalesce returns null only when every argument could be null.
    could_be_null = all(a.nullable or a.name in {"UNKNOWN", "NULL"} for a in args)
    return CypherType(combined.name, combined.element, could_be_null)


def _infer_head(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("head/last", args, 1, 1)
    _require_family("head/last", args[0], {"LIST"})
    return (args[0].element or UNKNOWN).optional() if args[0].name == "LIST" else UNKNOWN


def _infer_size(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("size", args, 1, 1)
    _require_family("size", args[0], {"LIST", "STRING"})
    return INTEGER.optional() if args[0].nullable else INTEGER


def _infer_abs(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("abs", args, 1, 1)
    _require_family("abs", args[0], {"INTEGER", "FLOAT", "NUMBER"})
    return args[0]


def _infer_to_string(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("toString", args, 1, 1)
    _require_family("toString", args[0], {"INTEGER", "FLOAT", "NUMBER", "BOOLEAN", "STRING"})
    return STRING.optional() if args[0].nullable else STRING


def _infer_rand(args: tuple[CypherType, ...]) -> CypherType:
    _require_arity("rand", args, 0, 0)
    return FLOAT


@dataclass(frozen=True)
class FunctionSpec:
    infer: Callable[[tuple[CypherType, ...]], CypherType]
    evaluate: Callable[[tuple[object, ...], random.Random], object]


def _coalesce(values, _rng):
    return next((v for v in values if v is not None), None)


def _head(values, _rng):
    return values[0][0] if values[0] else None


def _last(values, _rng):
    return values[0][-1] if values[0] else None


def _size(values, _rng):
    return len(values[0]) if values[0] is not None else None


def _abs(values, _rng):
    return abs(values[0]) if values[0] is not None else None


def _to_string(values, _rng):
    if values[0] is None:
        return None
    if isinstance(values[0], bool):
        return str(values[0]).lower()
    return str(values[0])


FUNCTIONS: dict[str, FunctionSpec] = {
    "coalesce": FunctionSpec(_infer_coalesce, _coalesce),
    "head": FunctionSpec(_infer_head, _head),
    "last": FunctionSpec(_infer_head, _last),
    "size": FunctionSpec(_infer_size, _size),
    "abs": FunctionSpec(_infer_abs, _abs),
    "tostring": FunctionSpec(_infer_to_string, _to_string),
    "rand": FunctionSpec(_infer_rand, lambda _values, rng: rng.random()),
}


def function_type(name: str, args: tuple[CypherType, ...]) -> CypherType:
    spec = FUNCTIONS.get(name.lower())
    if spec is None:
        return UNKNOWN  # Keep unregistered/UDF calls representable, not verified.
    return spec.infer(args)


def call_function(name: str, args: tuple[object, ...], rng: random.Random) -> object:
    spec = FUNCTIONS.get(name.lower())
    if spec is None:
        raise UnsupportedFunctionError(f"Function {name}() has no evaluator")
    return spec.evaluate(args, rng)


def infer_type(expr: ValueExpression, state: SymbolicState,
               computed: dict[Symbol, CypherType] | None = None) -> CypherType:
    """Infer types recursively, using observed schema as hints, not proof of existence."""
    computed = computed or {}
    if isinstance(expr, ValueLiteral):
        value = expr.value
        if value is None: return NULL
        if isinstance(value, bool): return BOOLEAN
        if isinstance(value, int): return INTEGER
        if isinstance(value, float): return FLOAT
        if isinstance(value, str): return STRING
        if isinstance(value, list):
            return LIST(join_types(*(infer_type(ValueLiteral(v), state) for v in value))) if value else LIST()
        return UNKNOWN
    if isinstance(expr, ParameterReference):
        return UNKNOWN
    if isinstance(expr, SymbolReference):
        if expr.symbol in computed: return computed[expr.symbol]
        for binding in state.scope:
            if binding.symbol == expr.symbol:
                if binding.value_type is not None: return binding.value_type
                if binding.value_kind == "NODE": return NODE
                if binding.value_kind == "RELATIONSHIP": return RELATIONSHIP
        if isinstance(expr.symbol, NodeSymbol): return NODE
        if isinstance(expr.symbol, RelationshipSymbol): return RELATIONSHIP
        return UNKNOWN
    if isinstance(expr, PropertyAccess):
        owner = infer_type(expr.owner, state, computed)
        if owner.name not in {"NODE", "RELATIONSHIP", "UNKNOWN"}:
            raise CypherTypeError(f"Cannot access property {expr.property_name!r} on {owner}")
        if isinstance(expr.owner, SymbolReference):
            symbol = expr.owner.symbol
            graph = state.graph.graph
            if owner.name == "NODE" and symbol in graph:
                types = [state.schema.node_property_type(label, expr.property_name)
                         for label in graph.nodes[symbol].get("labels", ())]
                known = [from_schema(t) for t in types if t]
                if known: return join_types(*known)
            if owner.name == "RELATIONSHIP":
                types = [data.get("types", ()) for _, _, data in graph.edges(data=True)
                         if data.get("symbol") == symbol]
                known = [from_schema(state.schema.relationship_property_type(t, expr.property_name))
                         for group in types for t in group if state.schema.relationship_property_type(t, expr.property_name)]
                if known: return join_types(*known)
        return UNKNOWN
    if isinstance(expr, Arithmetic):
        left = infer_type(expr.left, state, computed)
        right = infer_type(expr.right, state, computed)
        if left.name not in {"INTEGER", "FLOAT", "NUMBER", "UNKNOWN", "NULL"} or right.name not in {"INTEGER", "FLOAT", "NUMBER", "UNKNOWN", "NULL"}:
            # + also supports strings in Cypher; type it where both are known strings.
            if expr.operator == "+" and left.name == right.name == "STRING":
                return STRING.optional() if (left.nullable or right.nullable) else STRING
            raise CypherTypeError(f"Arithmetic {expr.operator} cannot combine {left} and {right}")
        if "UNKNOWN" in {left.name, right.name}: return UNKNOWN
        if "NULL" in {left.name, right.name}: return NULL
        name = "FLOAT" if expr.operator == "/" or "FLOAT" in {left.name, right.name} else (
            "NUMBER" if "NUMBER" in {left.name, right.name} else "INTEGER")
        return CypherType(name, nullable=left.nullable or right.nullable)
    if isinstance(expr, FunctionCall):
        return function_type(expr.name, tuple(infer_type(a, state, computed) for a in expr.arguments))
    if isinstance(expr, AggregateCall):
        arg = infer_type(expr.argument, state, computed) if expr.argument is not None else UNKNOWN
        if expr.name == "count": return INTEGER
        if expr.name == "collect": return LIST(CypherType(arg.name, arg.element, False))
        if expr.name in {"avg", "sum"}:
            _require_family(expr.name, arg, {"INTEGER", "FLOAT", "NUMBER"})
            if expr.name == "avg": return FLOAT.optional()
            return (FLOAT if arg.name == "FLOAT" else INTEGER if arg.name == "INTEGER" else NUMBER)
        if expr.name in {"min", "max"}: return arg.optional()
        raise UnsupportedFunctionError(f"Aggregate {expr.name} is unsupported")
    raise CypherTypeError(f"No type rule for {type(expr).__name__}")
