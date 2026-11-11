
"""Data types for graph-independent symbolic Cypher analysis.

Supplies the data for networkx schema graphs in graph.py

GraphSchema holds information about possible graph structure.
SymbolicState is the starting point for later symbolic clause transformations.
"""

from dataclasses import dataclass, field
from typing import  Any, Literal, Mapping



# Compatibility with code importing `AstNode` from this package. This is only
# a type alias for the serialized JSON object, NOT a reconstructed AST class.
AstNode = dict[str, Any]
SchemaCompleteness = Literal["observed", "exhaustive"]


@dataclass(frozen=True)
class RelationshipSignature:
    """One schema signature (start label)-[type]->(end label).

    A relationship TYPE can have multiple signatures; no concrete relationship
    identities or database records are contained here.
    """

    start_label: str
    relationship_type: str
    end_label: str


@dataclass(frozen=True)
class GraphSchema:
    """Typed view of the schema supplied in a request.

    'observed' (the default) means missing properties/signatures are unknown,
    NOT forbidden. 'exhaustive' is an explicit caller assertion. A complete
    schema still needs separately stated existence/uniqueness constraints to
    justify stronger claims; this class never infers them from property lists.

    Metadata and extra schema fields are preserved for future constraints.
    """

    node_properties: Mapping[str, Mapping[str, str]]
    relationship_properties: Mapping[str, Mapping[str, str]]
    relationships: tuple[RelationshipSignature, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    completeness: SchemaCompleteness = "observed"
    extras: Mapping[str, Any] = field(default_factory=dict)

    def node_property_type(self, label: str, property_name: str) -> str | None:
        return self.node_properties.get(label, {}).get(property_name)

    def relationship_property_type(self, rel_type: str, property_name: str) -> str | None:
        return self.relationship_properties.get(rel_type, {}).get(property_name)

    def signatures_for(self, relationship_type: str) -> tuple[RelationshipSignature, ...]:
        """Keep multiple directions/endpoints for the same relationship type."""
        return tuple(
            signature for signature in self.relationships
            if signature.relationship_type == relationship_type
        )


@dataclass(frozen=True)
class AnalysisRequest:
    """Validated `build_lean_request()` envelope, not a re-parsed query.

    query_ast holds the ORIGINAL CypherAST JSON dictionaries. Nested `args`,
    `Map.entries`, lists, null fields, and optional AST metadata are retained.
    The validated top-level form is Cypher -> Query -> clauses; other forms
    (such as UNION roots) are not silently treated as sequential clauses.
    """

    protocol_version: int
    request_id: str
    dialect: str
    query_text: str
    query_ast: tuple[AstNode, ...]
    schema: GraphSchema
    parameters: Mapping[str, Any]
    lineage: Any = None  # Optional/advisory, never the source of scope truth.
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def clauses(self) -> tuple[AstNode, ...]:
        """Get the already-parsed top-level clause dictionaries, in order."""
        root = self.query_ast[0]
        return tuple(root["args"]["this"]["args"]["clauses"])


# Mathematical symbol names, never actual Neo4j entity IDs.
@dataclass(frozen=True)
class NodeSymbol:
    name: str


@dataclass(frozen=True)
class RelationshipSymbol:
    name: str


@dataclass(frozen=True)
class ScalarSymbol:
    name: str


Symbol = NodeSymbol | RelationshipSymbol | ScalarSymbol


@dataclass(frozen=True)
class Binding:
    """A Cypher variable associated with a symbolic term, not a real row."""

    variable: str
    symbol: Symbol


class BagExpression:
    """Base class for symbolic bag expressions; not materialized rows."""


@dataclass(frozen=True)
class UnitBag(BagExpression):
    """One empty variable assignment of multiplicity one."""


@dataclass(frozen=True)
class SymbolicState:
    """State threaded through later symbolic clause transformations.

    MATCH will introduce symbolic graph witnesses; WHERE will add conditions;
    WITH, DISTINCT and RETURN will transform symbolic bag expressions.
    Compiler initializes the graph; transformations replace immutable states.
    """

    schema: GraphSchema
    scope: tuple[Binding, ...]
    bag: BagExpression
    graph: "GraphSnapshot | None" = None  # NetworkX symbolic-pattern snapshot.

