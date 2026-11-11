"""To proccess input requests"""
"""Read the JSON returned by the existing `build_lean_request()` function.

No Cypher parsing, no rehydration of CypherAST objects, no query execution.
The request envelope and schema fields are checked. The upstream parser is
responsible for supplying one Cypher -> Query -> clauses AST per request;
this loader does not revalidate or reconstruct its nested nodes.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from graph_query_analysis.model import AnalysisRequest, GraphSchema, RelationshipSignature


class InputValidationError(ValueError):
    """The request does not follow the supported JSON input contract."""


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputValidationError(f"{path} must be a JSON object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputValidationError(f"{path} must be a nonempty string")
    return value



def _read_properties(section: Any, path: str) -> Mapping[str, Mapping[str, str]]:
    raw = _object(section, path)
    properties: dict[str, Mapping[str, str]] = {}
    for label, entries in raw.items():
        _text(label, f"{path} key")
        if not isinstance(entries, list):
            raise InputValidationError(f"{path}.{label} must be a list")
        found: dict[str, str] = {}
        for i, entry in enumerate(entries):
            item = _object(entry, f"{path}.{label}[{i}]")
            name = _text(item.get("property"), f"{path}.{label}[{i}].property")
            type_name = _text(item.get("type"), f"{path}.{label}[{i}].type")
            if name in found and found[name] != type_name:
                raise InputValidationError(
                    f"Conflicting types for {path}.{label}.{name}: "
                    f"{found[name]} and {type_name}"
                )
            found[name] = type_name
        properties[label] = MappingProxyType(found)
    return MappingProxyType(properties)


def _read_schema(raw: Any, completeness: Any) -> GraphSchema:
    data = _object(raw, "schema")
    if completeness not in ("observed", "exhaustive"):
        raise InputValidationError("schema_completeness must be 'observed' or 'exhaustive'")
    node_props = _read_properties(data.get("node_props"), "schema.node_props")
    rel_props = _read_properties(data.get("rel_props"), "schema.rel_props")

    rels = data.get("relationships")
    if not isinstance(rels, list):
        raise InputValidationError("schema.relationships must be a list")
    signatures: list[RelationshipSignature] = []
    for i, entry in enumerate(rels):
        rel = _object(entry, f"schema.relationships[{i}]")
        signatures.append(RelationshipSignature(
            start_label=_text(rel.get("start"), f"schema.relationships[{i}].start"),
            relationship_type=_text(rel.get("type"), f"schema.relationships[{i}].type"),
            end_label=_text(rel.get("end"), f"schema.relationships[{i}].end"),
        ))

    metadata = data.get("metadata")
    if metadata is None:
        metadata = {}
    _object(metadata, "schema.metadata")
    known = {"node_props", "rel_props", "relationships", "metadata"}
    return GraphSchema(
        node_properties=node_props,
        relationship_properties=rel_props,
        relationships=tuple(signatures),
        metadata=MappingProxyType(deepcopy(metadata)),
        completeness=completeness,
        extras=MappingProxyType(deepcopy({k: v for k, v in data.items() if k not in known})),
    )


def _read_request(raw: Any, index: int) -> AnalysisRequest:
    data = _object(raw, f"requests[{index}]")
    version = data.get("protocol_version")
    if type(version) is not int or version != 1:
        raise InputValidationError("protocol_version must be integer 1")
    request_id = _text(data.get("request_id"), "request_id")
    dialect = _text(data.get("dialect"), "dialect")
    if dialect != "neo4j25":
        raise InputValidationError(f"Unsupported dialect {dialect!r}; expected 'neo4j25'")
    query_text = _text(data.get("query_text"), "query_text")
    query_ast = tuple(data.get("query_ast"))

    parameters = data.get("parameters", {})
    if parameters is None:
        parameters = {}
    _object(parameters, "parameters")

    known = {
        "protocol_version", "request_id", "dialect", "query_text",
        "query_ast", "schema", "parameters", "lineage", "schema_completeness",
    }
    return AnalysisRequest(
        protocol_version=version,
        request_id=request_id,
        dialect=dialect,
        query_text=query_text,
        query_ast=query_ast,
        schema=_read_schema(data.get("schema"), data.get("schema_completeness", "observed")),
        parameters=MappingProxyType(deepcopy(parameters)),
        lineage=deepcopy(data.get("lineage")),
        extras=MappingProxyType(deepcopy({k: v for k, v in data.items() if k not in known})),
    )


def parse_requests(payload: Any) -> tuple[AnalysisRequest, ...]:
    """Accept `build_lean_request()` output (list of dicts) or a single dict."""
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise InputValidationError("Expected a request object or nonempty request list")
    return tuple(_read_request(request, i) for i, request in enumerate(payload))


def load_requests(path: str | Path) -> tuple[AnalysisRequest, ...]:
    """Read an existing request JSON file; no parser dependency is required."""
    with Path(path).open("r", encoding="utf-8") as stream:
        return parse_requests(json.load(stream))
