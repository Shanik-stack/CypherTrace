"""NetworkX views of a schema and of *symbolic pattern requirements*.

This module does NOT model a concrete Neo4j database. A graph node is a
symbolic assignment slot, not a database record. A graph edge is a pattern
occurrence, not an existing database relationship.

GraphSnapshot implements the requested copy-on-transition policy: each new
snapshot contains a `copy.deepcopy` of the previous NetworkX MultiDiGraph,
including nested node/edge attributes. The bag expression and Cypher scope
remain in `model.py` and must be transformed separately.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterable, Literal, TYPE_CHECKING

import networkx as nx

from graph_query_analysis.model import GraphSchema, Symbol, NodeSymbol, ValueSymbol, RelationshipSymbol
if TYPE_CHECKING:
    from graph_query_analysis.expressions import Predicate


Direction = Literal["OUTGOING", "INCOMING", "BOTH"]


@dataclass(frozen=True)
class NodeRequirement:
    """Constraints on a possible node bound to `symbol`.

    `predicates` are symbolic conditions introduced by this pattern occurrence.
    MatchBag is their semantic owner; NetworkX is a descriptive snapshot.

    Distinct NodeSymbols are NOT automatically distinct database nodes.
    """

    symbol: Symbol
    labels: tuple[str, ...] = ()
    predicates: tuple["Predicate", ...] = ()


@dataclass(frozen=True)
class RelationshipRequirement:
    """One syntactic relationship occurrence between symbolic node slots.

    `symbol` denotes the relationship variable/witness, while the NetworkX
    edge key identifies this *occurrence*. Repeated uses of one symbol remain
    separate pattern occurrences; the symbolic semantics will impose any
    identity/uniqueness constraints later.

    `BOTH` means Cypher's undirected pattern: the NetworkX arc is only a
    storage convention, NOT a required graph direction.
    """

    symbol: Symbol
    left: NodeSymbol | ValueSymbol
    right: NodeSymbol | ValueSymbol
    types: tuple[str, ...] = ()
    direction: Direction = "OUTGOING"
    predicates: tuple["Predicate", ...] = ()


def build_schema_graph(schema: GraphSchema) -> nx.MultiDiGraph:
    """Create a NetworkX *schema* graph (labels and relationship signatures).

    This graph is descriptive unless `schema.completeness` says otherwise;
    no graph instances, endpoints, or constraints are invented.
    """
    graph = nx.MultiDiGraph(role="schema", completeness=schema.completeness)
    labels = set(schema.node_properties)
    labels.update(signature.start_label for signature in schema.relationships)
    labels.update(signature.end_label for signature in schema.relationships)

    for label in sorted(labels):
        graph.add_node(
            label,
            properties=dict(schema.node_properties.get(label, {})),
        )

    for index, signature in enumerate(schema.relationships):
        graph.add_edge(
            signature.start_label,
            signature.end_label,
            key=f"signature:{index}",
            relationship_type=signature.relationship_type,
            properties=dict(schema.relationship_properties.get(signature.relationship_type, {})),
        )

    return graph


def _add_node(graph: nx.MultiDiGraph, req: NodeRequirement) -> None:
    """Accumulate constraints without overwriting older requirements."""
    if not isinstance(req.symbol, (NodeSymbol, ValueSymbol)):
        raise TypeError("NodeRequirement.symbol must be a node-valued symbol")

    if req.symbol not in graph:
        graph.add_node(req.symbol, labels=(), predicates=())

    attrs = graph.nodes[req.symbol]
    attrs["labels"] = tuple(dict.fromkeys((*attrs.get("labels", ()), *req.labels)))
    attrs["predicates"] = (*attrs.get("predicates", ()), *deepcopy(req.predicates))


def _add_relationship(graph: nx.MultiDiGraph, req: RelationshipRequirement) -> None:
    if not isinstance(req.symbol, (RelationshipSymbol, ValueSymbol)):
        raise TypeError("RelationshipRequirement.symbol must be a relationship-valued symbol")
    if not isinstance(req.left, (NodeSymbol, ValueSymbol)) or not isinstance(req.right, (NodeSymbol, ValueSymbol)):
        raise TypeError("Relationship endpoints must be node-valued symbols")
    if req.direction not in ("OUTGOING", "INCOMING", "BOTH"):
        raise ValueError(f"Unsupported relationship direction: {req.direction!r}")

    # MATCH may refer to nodes with no explicit label restrictions.
    _add_node(graph, NodeRequirement(req.left))
    _add_node(graph, NodeRequirement(req.right))

    source, target = (req.right, req.left) if req.direction == "INCOMING" else (req.left, req.right)
    edge_index = graph.graph["next_occurrence"]
    graph.graph["next_occurrence"] = edge_index + 1
    graph.add_edge(
        source,
        target,
        key=f"occurrence:{edge_index}",
        symbol=req.symbol,
        types=tuple(req.types),
        direction=req.direction,
        left=req.left,
        right=req.right,
        predicates=deepcopy(req.predicates),
    )


@dataclass(frozen=True)
class GraphSnapshot:
    """One version of the accumulated symbolic pattern graph.

    The underlying NetworkX object is mutable, so it is kept private. `graph`
    returns a defensive deep copy. `advance` clones exactly once before
    accumulating the new clause's graph requirements.

    For a clause that changes only scope/bag semantics, call `advance()` with
    no requirements: the graph shape stays the same, but is a new snapshot.
    """

    _graph: nx.MultiDiGraph = field(repr=False, compare=False)

    @classmethod
    def empty(cls) -> "GraphSnapshot":
        return cls(nx.MultiDiGraph(role="symbolic_pattern", next_occurrence=0))

    @property
    def graph(self) -> nx.MultiDiGraph:
        """Inspect a snapshot safely; edits to this returned copy do not persist."""
        return deepcopy(self._graph)

    def advance(
        self,
        *,
        nodes: Iterable[NodeRequirement] = (),
        relationships: Iterable[RelationshipRequirement] = (),
    ) -> "GraphSnapshot":
        """Return a fully independent graph snapshot for the next clause."""
        updated = deepcopy(self._graph)
        for node in nodes:
            _add_node(updated, node)
        for relationship in relationships:
            _add_relationship(updated, relationship)
        return GraphSnapshot(updated)
