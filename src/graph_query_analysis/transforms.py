"""Implementing the clause transformatoins 
like MATCH WHERE WITH RETURN etc"""
"""Clause-by-clause, graph-independent interpretation of serialized CypherAST.

This module consumes the dictionaries already produced by ``build_lean_request``.
It does not import CypherAST, inspect graph records, evaluate symbolic bags, or
invoke a solver. A successful transformation builds a *description* of what
would be matched/projected on any compatible graph.

Implemented fragment (Neo4j Cypher 25):
  * fixed-length node/relationship patterns in mandatory MATCH, including
    direction, simple conjunctions of labels, and property maps;
  * MATCH/inline WHERE for the predicate forms recognized below;
  * WITH / RETURN projection, DISTINCT, and direct aggregate calls;
  * WITH ... WHERE evaluated against the projected scope.

Unknown syntax is rejected explicitly. OPTIONAL MATCH, variable-length paths,
path-variable bindings, ordering, slicing, subqueries, and writes are NOT yet
modeled. An expression tree is not itself a correctness proof or a solver.
"""

from dataclasses import replace
from itertools import combinations
from typing import Any, Mapping

from graph_query_analysis.expressions import (
    AggregateBag,
    AggregateCall,
    AllOf,
    AnyOf,
    Arithmetic,
    Comparison,
    DistinctBag,
    ExtendBag,
    FilterBag,
    FunctionCall,
    IsNull,
    MatchBag,
    Negation,
    ParameterReference,
    Predicate,
    ProjectBag,
    Projection,
    PropertyAccess,
    SymbolReference,
    TruthLiteral,
    ValueExpression,
    ValueLiteral,
)
from graph_query_analysis.graph import NodeRequirement, RelationshipRequirement
from graph_query_analysis.model import (
    Binding,
    NodeSymbol,
    RelationshipSymbol,
    ScalarSymbol,
    SymbolicState,
)


class TransformError(ValueError):
    """Malformed or out-of-scope serialized CypherAST in the supported subset."""


class UnsupportedCypherError(NotImplementedError):
    """Valid-looking syntax outside the currently implemented semantics."""


class VariableScopeError(TransformError):
    """A Cypher variable is unavailable at this point in the query."""


_AGGREGATES = frozenset(("count", "sum", "avg", "min", "max", "collect"))
_COMPARISONS = {
    "EQ": "=", "Eq": "=", "Equal": "=", "Equals": "=",
    "NEQ": "<>", "Neq": "<>", "NotEqual": "<>",
    "GT": ">", "Gt": ">", "GreaterThan": ">",
    "GTE": ">=", "Gte": ">=", "GreaterThanOrEqual": ">=",
    "LT": "<", "Lt": "<", "LessThan": "<",
    "LTE": "<=", "Lte": "<=", "LessThanOrEqual": "<=",
}
_ARITHMETIC = {"Add": "+", "Sub": "-", "Mul": "*", "Div": "/", "Mod": "%"}


def _node(value: Any, context: str) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise TransformError(f"{context}: expected an AST node with a type")
    args = value.get("args")
    if not isinstance(args, dict):
        raise TransformError(f"{context}: expected an AST args object")
    return value["type"], args


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TransformError(message)


def _unsupported(context: str, value: Any) -> None:
    raise UnsupportedCypherError(f"{context} is not supported yet: {value!r}")


def _reject_unknown(args: Mapping[str, Any], allowed: set[str], context: str) -> None:
    """Never silently discard unfamiliar semantic flags from newer ASTs."""
    for name, value in args.items():
        if name not in allowed and value is not None:
            _unsupported(f"{context}.{name}", value)


def _identifier(value: Any, context: str) -> str:
    kind, args = _node(value, context)
    if kind != "Identifier" or not isinstance(args.get("this"), str) or not args["this"]:
        raise TransformError(f"{context}: expected a nonempty Identifier")
    return args["this"]


def _scope(state: SymbolicState) -> dict[str, Binding]:
    return {binding.variable: binding for binding in state.scope}


def _resolve(state: SymbolicState, name: str):
    binding = _scope(state).get(name)
    if binding is None:
        raise VariableScopeError(f"Variable {name!r} is not in scope")
    return binding.symbol



def _fresh_symbol(state: SymbolicState, name: str | None, *, relationship: bool,
                  reserved: set[str]) -> NodeSymbol | RelationshipSymbol:
    """Fresh *assignment slots*, not database identities.

    The graph snapshot retains earlier clause slots, including out-of-scope
    ones. A name reintroduced after WITH must not accidentally identify with
    its earlier binding. Generated names contain NUL, which cannot appear in a
    Cypher identifier. ``reserved`` also covers allocations in this clause.
    """
    graph = state.graph.graph
    used = set(reserved)
    if relationship:
        used.update(data["symbol"].name for _, _, data in graph.edges(data=True))
        used.update(b.symbol.name for b in state.scope if isinstance(b.symbol, RelationshipSymbol))
    else:
        used.update(sym.name for sym in graph.nodes if isinstance(sym, NodeSymbol))
        used.update(b.symbol.name for b in state.scope if isinstance(b.symbol, NodeSymbol))
    stem = name if name is not None else ("\x00rel" if relationship else "\x00node")
    candidate = stem
    n = 1
    while candidate in used:
        candidate = f"{stem}\x00{n}"
        n += 1
    reserved.add(candidate)
    return RelationshipSymbol(candidate) if relationship else NodeSymbol(candidate)


def _value(ast: Any, state: SymbolicState, *, aggregate: bool = False,
           variables: Mapping[str, ValueExpression] | None = None) -> ValueExpression:
    kind, args = _node(ast, "value")
    if kind == "Identifier":
        name = _identifier(ast, "variable reference")
        if variables is not None and name in variables:
            return variables[name]
        return SymbolReference(_resolve(state, name))
    if kind == "Property":
        prop = args.get("name")
        _require(isinstance(prop, str) and bool(prop), "Property.name must be a string")
        return PropertyAccess(_value(args.get("this"), state, variables=variables), prop)
    if kind in ("String", "StringLiteral"):
        literal = args.get("this")
        _require(isinstance(literal, str), "String literal must contain a string")
        return ValueLiteral(literal)
    if kind in ("Integer", "Int", "IntegerLiteral"):
        raw = args.get("this")
        try:
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise ValueError
            result = int(raw)
        except ValueError as exc:
            raise TransformError(f"Invalid integer literal: {raw!r}") from exc
        return ValueLiteral(result)
    if kind in ("Float", "Double", "FloatLiteral"):
        raw = args.get("this")
        try:
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                raise ValueError
            result = float(raw)
        except ValueError as exc:
            raise TransformError(f"Invalid floating-point literal: {raw!r}") from exc
        return ValueLiteral(result)
    if kind == "Boolean":
        raw = args.get("this")
        if isinstance(raw, bool):
            return ValueLiteral(raw)
        if isinstance(raw, str) and raw.lower() in ("true", "false"):
            return ValueLiteral(raw.lower() == "true")
        raise TransformError("Boolean literal must be true or false")
    if kind == "Null":
        return ValueLiteral(None)
    if kind == "Parameter":
        name = args.get("this") or args.get("name")
        _require(isinstance(name, str) and bool(name), "Parameter requires a name")
        return ParameterReference(name)
    if kind in _ARITHMETIC:
        return Arithmetic(_ARITHMETIC[kind],
                          _value(args.get("this"), state, variables=variables),
                          _value(args.get("expression"), state, variables=variables))
    if kind == "FunctionCall":
        name = args.get("name")
        _require(isinstance(name, str) and bool(name), "FunctionCall requires a name")
        name_lower = name.lower()
        raw_arguments = args.get("expressions")
        _require(isinstance(raw_arguments, list), "FunctionCall.expressions must be a list")
        if name_lower in _AGGREGATES:
            if not aggregate:
                raise UnsupportedCypherError("Nested/inline aggregates are not supported")
            if len(raw_arguments) == 0 and name_lower == "count":
                arg = None
            elif len(raw_arguments) == 1 and _node(raw_arguments[0], "aggregate argument")[0] == "Star":
                _require(name_lower == "count", "Only count(*) accepts a star")
                arg = None
            elif len(raw_arguments) == 1:
                arg = _value(raw_arguments[0], state, variables=variables)
            else:
                raise UnsupportedCypherError(f"Unsupported {name} argument list")
            if name_lower != "count" and arg is None:
                raise TransformError(f"{name} requires an argument")
            distinct = args.get("distinct")
            _require(distinct is None or isinstance(distinct, bool),
                     "Aggregate DISTINCT must be boolean or null")
            return AggregateCall(name_lower, arg, distinct=bool(distinct))
        if args.get("distinct") not in (None, False):
            _unsupported("DISTINCT on scalar function", name)
        return FunctionCall(name, tuple(_value(a, state, variables=variables) for a in raw_arguments))
    _unsupported("Value AST type", kind)


def _predicate(ast: Any, state: SymbolicState, *,
               variables: Mapping[str, ValueExpression] | None = None) -> Predicate:
    kind, args = _node(ast, "predicate")
    if kind == "Where":
        return _predicate(args["this"], state, variables=variables)
    if kind in _COMPARISONS:
        return Comparison(_COMPARISONS[kind],
                          _value(args.get("this"), state, variables=variables),
                          _value(args.get("expression"), state, variables=variables))
    if kind in ("And", "Or"):
        left = _predicate(args.get("this"), state, variables=variables)
        right = _predicate(args.get("expression"), state, variables=variables)
        return AllOf((left, right)) if kind == "And" else AnyOf((left, right))
    if kind == "Not":
        return Negation(_predicate(args.get("this"), state, variables=variables))
    if kind in ("IsNull", "IsNotNull"):
        return IsNull(_value(args.get("this"), state, variables=variables), negated=kind == "IsNotNull")
    if kind == "Boolean":
        literal = _value(ast, state, variables=variables)
        return TruthLiteral(literal.value)
    if kind == "Null":
        return TruthLiteral(None)
    _unsupported("Predicate AST type", kind)


def _property_predicates(properties: Any, symbol, state: SymbolicState) -> tuple[Predicate, ...]:
    if properties is None:
        return ()
    kind, args = _node(properties, "pattern property map")
    if kind != "Map":
        _unsupported("Pattern property container", kind)
    entries = args.get("entries")
    _require(isinstance(entries, list), "Map.entries must be a list")
    results: list[Predicate] = []
    for entry in entries:
        _require(isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str),
                 "Map entries must be [property_name, value_ast] pairs")
        results.append(Comparison("=", PropertyAccess(SymbolReference(symbol), entry[0]),
                                  _value(entry[1], state)))
    return tuple(results)


def _all(conditions: list[Predicate]) -> Predicate | None:
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else AllOf(tuple(conditions))


def transform_match(state: SymbolicState, clause: dict[str, Any]) -> SymbolicState:
    kind, args = _node(clause, "MATCH")
    _require(kind == "Match", f"Expected MATCH, got {kind!r}")
    _reject_unknown(args, {"pattern", "optional", "where", "hints", "search"}, "MATCH")
    if args.get("optional") not in (None, False):
        _unsupported("OPTIONAL MATCH", kind)
    if args.get("hints") or args.get("search"):
        _unsupported("MATCH hints/search", args)
    pattern_kind, pattern_args = _node(args.get("pattern"), "MATCH.pattern")
    _require(pattern_kind == "Pattern", "MATCH.pattern must be Pattern")
    _reject_unknown(pattern_args, {"paths"}, "Pattern")
    paths = pattern_args.get("paths")
    _require(isinstance(paths, list) and bool(paths), "MATCH requires nonempty Pattern.paths")

    bindings = _scope(state)
    nodes: list[NodeRequirement] = []
    relationships: list[RelationshipRequirement] = []
    predicates: list[Predicate] = []
    reserved_nodes: set[str] = set()
    reserved_rels: set[str] = set()

    def bind_node(ast: Any) -> NodeSymbol:
        _, node_args = _node(ast, "NodePattern")
        _reject_unknown(node_args, {"variable", "labels", "properties", "where"}, "NodePattern")
        var_ast = node_args.get("variable")
        name = _identifier(var_ast, "NodePattern.variable") if var_ast is not None else None
        if name is not None and name in bindings:
            symbol = bindings[name].symbol
            if not isinstance(symbol, NodeSymbol):
                raise TransformError(f"Variable {name!r} is already bound to a non-node")
        else:
            symbol = _fresh_symbol(state, name, relationship=False, reserved=reserved_nodes)
            if name is not None:
                bindings[name] = Binding(name, symbol)

        labels = node_args.get("labels")
        labels_tuple: tuple[str, ...] = ()
        if labels is not None:
            label_kind, label_args = _node(labels, "NodePattern.labels")
            _require(label_kind == "LabelExpression", "Expected LabelExpression")
            if label_args.get("expression") is not None:
                _unsupported("Boolean label expression", labels)
            raw_labels = label_args.get("labels")
            _require(isinstance(raw_labels, list) and all(isinstance(x, str) for x in raw_labels),
                     "LabelExpression.labels must be strings")
            labels_tuple = tuple(raw_labels)
        # Property-map values can reference previously or currently bound nodes.
        state_in_scope = replace(state, scope=tuple(bindings.values()))
        required = _property_predicates(node_args.get("properties"), symbol, state_in_scope)
        if node_args.get("where") is not None:
            required += (_predicate(node_args["where"], state_in_scope),)
        nodes.append(NodeRequirement(symbol, labels_tuple, required))
        predicates.extend(required)
        return symbol

    def bind_relationship(ast: Any, left: NodeSymbol, right: NodeSymbol) -> None:
        _, rel_args = _node(ast, "RelationshipPattern")
        _reject_unknown(rel_args, {"variable", "types", "properties", "direction", "min_hops",
                                   "max_hops", "variable_length", "where", "memgraph_quantifier",
                                   "memgraph_weight_expr", "memgraph_total_weight"}, "RelationshipPattern")
        for flag in ("min_hops", "max_hops", "variable_length", "memgraph_quantifier",
                     "memgraph_weight_expr", "memgraph_total_weight"):
            if rel_args.get(flag) is not None:
                _unsupported("Variable-length/weighted relationship", flag)
        direction = rel_args.get("direction") or "BOTH"
        if direction not in ("OUTGOING", "INCOMING", "BOTH"):
            _unsupported("Relationship direction", direction)
        types = rel_args.get("types") or []
        _require(isinstance(types, list) and all(isinstance(t, str) for t in types),
                 "RelationshipPattern.types must be a list of names")
        var_ast = rel_args.get("variable")
        name = _identifier(var_ast, "RelationshipPattern.variable") if var_ast is not None else None
        if name is not None and name in bindings:
            symbol = bindings[name].symbol
            if not isinstance(symbol, RelationshipSymbol):
                raise TransformError(f"Variable {name!r} is already bound to a non-relationship")
        else:
            symbol = _fresh_symbol(state, name, relationship=True, reserved=reserved_rels)
            if name is not None:
                bindings[name] = Binding(name, symbol)
        state_in_scope = replace(state, scope=tuple(bindings.values()))
        required = _property_predicates(rel_args.get("properties"), symbol, state_in_scope)
        if rel_args.get("where") is not None:
            required += (_predicate(rel_args["where"], state_in_scope),)
        relationships.append(RelationshipRequirement(symbol, left, right,
                                                     tuple(types), direction, required))
        predicates.extend(required)

    for path in paths:
        path_kind, path_args = _node(path, "PathPattern")
        _require(path_kind == "PathPattern", "Only PathPattern is supported")
        _reject_unknown(path_args, {"variable", "elements"}, "PathPattern")
        if path_args.get("variable") is not None:
            _unsupported("Named path variables", path_args["variable"])
        elements = path_args.get("elements")
        _require(isinstance(elements, list) and len(elements) % 2 == 1 and len(elements) >= 1,
                 "PathPattern.elements must alternate node, relationship, node")
        if _node(elements[0], "path element")[0] != "NodePattern":
            raise TransformError("Path must begin with NodePattern")
        left = bind_node(elements[0])
        for i in range(1, len(elements), 2):
            if _node(elements[i], "path element")[0] != "RelationshipPattern":
                raise TransformError("Expected RelationshipPattern")
            if _node(elements[i + 1], "path element")[0] != "NodePattern":
                raise TransformError("Expected NodePattern")
            right = bind_node(elements[i + 1])
            bind_relationship(elements[i], left, right)
            left = right

    # Default relationship uniqueness applies across all patterns in this MATCH.
    for a, b in combinations(relationships, 2):
        predicates.append(Comparison(
            "<>", SymbolReference(a.symbol), SymbolReference(b.symbol)
        ))

    scoped = replace(state, scope=tuple(bindings.values()))
    if args.get("where") is not None:
        predicates.append(_predicate(args["where"], scoped))
    bag = MatchBag(state.bag, tuple(nodes), tuple(relationships))
    condition = _all(predicates)
    if condition is not None:
        bag = FilterBag(bag, condition)
    return replace(scoped, bag=bag, graph=state.graph.advance(
        nodes=nodes, relationships=relationships,
    ))


def _default_alias(ast: Any) -> str | None:
    """Only a bare identifier supplies an implicit variable name.

    For other unaliased expressions, retain the expression and output position
    instead of inventing an incorrect column header.
    """
    kind, _ = _node(ast, "projection")
    return _identifier(ast, "projection") if kind == "Identifier" else None



def _bag_depth(bag) -> int:
    depth = 0
    while hasattr(bag, "source"):
        depth += 1
        bag = bag.source
    return depth


def _projection_items(state: SymbolicState, items: Any):
    _require(isinstance(items, list) and bool(items), "Projection requires a nonempty expressions list")
    projections: list[Projection] = []
    bindings: list[Binding] = []
    aggregations: list[Projection] = []
    groupings: list[Projection] = []
    for index, item in enumerate(items):
        kind, args = _node(item, "projection item")
        if kind == "Alias":
            alias = _identifier(args.get("alias"), "projection alias")
            value_ast = args.get("this")
        else:
            alias = _default_alias(item)
            value_ast = item
        if alias is not None and any(binding.variable == alias for binding in bindings):
            raise TransformError(f"Duplicate projection alias {alias!r}")
        expr = _value(value_ast, state, aggregate=True)

        if isinstance(expr, SymbolReference):
            out_symbol = expr.symbol
        else:
            out_symbol = ScalarSymbol(f"\x00computed:{_bag_depth(state.bag)}:{index}")
        projections.append(Projection(alias, expr, out_symbol))
        if alias is not None:
        bindings.append(Binding(alias, out_symbol))
    return tuple(projections), tuple(bindings)


def _names_referenced(ast: Any) -> set[str]:
    """Read identifiers in an already-parsed WHERE expression; no Cypher parsing."""
    if isinstance(ast, list):
        return set().union(*(_names_referenced(x) for x in ast)) if ast else set()
    if isinstance(ast, dict):
        if ast.get("type") == "Identifier":
            return {ast["args"]["this"]}
        return set().union(*(_names_referenced(x) for x in ast.values()))
    return set()


def _project_clause(state: SymbolicState, clause: dict[str, Any], *, with_clause: bool) -> SymbolicState:
    kind, args = _node(clause, "WITH/RETURN")
    expected = "With" if with_clause else "Return"
    _require(kind == expected, f"Expected {expected}, got {kind!r}")
    _reject_unknown(args, {"expressions", "distinct", "where", "order", "skip",
                           "limit", "group_by"}, expected)
    for modifier in ("order", "skip", "limit", "group_by"):
        if args.get(modifier) is not None:
            _unsupported(f"{expected}.{modifier}", args[modifier])
    distinct = args.get("distinct")
    _require(distinct is None or isinstance(distinct, bool), "DISTINCT must be boolean or null")
    if not with_clause and args.get("where") is not None:
        _unsupported("RETURN WHERE", args["where"])

    columns, bindings = _projection_items(state, args.get("expressions"))
    has_aggregates = any(isinstance(c.expression, AggregateCall) for c in columns)
    where = args.get("where") if with_clause else None
    exported_names = {b.variable for b in bindings}
    uses_hidden_input = where is not None and bool(_names_referenced(where) - exported_names)

    if has_aggregates:
        # Aggregation doesn't retain arbitrary source rows; a grouped WHERE
        # cannot refer to their non-projected bindings without added semantics.
        if uses_hidden_input:
            raise UnsupportedCypherError(
                "WITH WHERE refers to non-projected input after aggregation"
            )
        bag = AggregateBag(state.bag, columns)
    elif uses_hidden_input:
        # Compute aliases ONCE, keeping input symbols until the WHERE is applied.
        extended = ExtendBag(state.bag, columns)
        combined = {b.variable: b for b in state.scope}
        combined.update({b.variable: b for b in bindings})  # New aliases shadow old names.
        extended_state = replace(state, scope=tuple(combined.values()))
        predicate = _predicate(where, extended_state)
        # Project from the materialized results, not from original expressions:
        # this avoids duplicating nondeterministic function calls such as rand().
        exposed = tuple(Projection(c.alias, SymbolReference(c.symbol), c.symbol) for c in columns)
        bag = ProjectBag(FilterBag(extended, predicate), exposed)
    else:
        bag = ProjectBag(state.bag, columns)

    if distinct:
        bag = DistinctBag(bag)

    result = replace(state, bag=bag, scope=bindings, graph=state.graph.advance())
    if where is not None and (has_aggregates or not uses_hidden_input):
        result = replace(result, bag=FilterBag(result.bag, _predicate(where, result)))
    return result



def transform_with(state: SymbolicState, clause: dict[str, Any]) -> SymbolicState:
    """Projection, optional grouping, DISTINCT, scope reset, and post-WITH WHERE."""
    return _project_clause(state, clause, with_clause=True)


def transform_return(state: SymbolicState, clause: dict[str, Any]) -> SymbolicState:
    """Construct final result bag; caller must not append more clauses."""
    return _project_clause(state, clause, with_clause=False)


def transform_clause(state: SymbolicState, clause: dict[str, Any]) -> SymbolicState:
    """Single-step dispatcher for a future compiler.py.

    Unsupported clauses are deliberately errors: returning the input state
    unchanged would silently assert incorrect semantics.
    """
    kind, _ = _node(clause, "clause")
    if kind == "Match":
        return transform_match(state, clause)
    if kind == "With":
        return transform_with(state, clause)
    if kind == "Return":
        return transform_return(state, clause)
    _unsupported("Cypher clause", kind)
