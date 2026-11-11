"""Deterministic, schema-only explanations of compiled Cypher query states.

This is a *reporter*, not a second Cypher interpreter, a solver, or an LLM.
It inspects the immutable operators built by transforms.py and the formulas
already derived by semantics.py. It does not inspect concrete graph records.

Report statements distinguish operator guarantees from *possible* effects.
The schema (especially an observed schema) does not establish whether any
matching data exists or whether the query satisfies a user's intent.
"""

from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Literal, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from graph_query_analysis.reporting.core import Section, Insight

from graph_query_analysis.compiler import compile_request
from graph_query_analysis.expressions import (
    AggregateBag, AggregateCall, DistinctBag, ExtendBag, FilterBag, MatchBag,
    ParameterReference, Predicate, ProjectBag, Projection, SliceBag, SortBag,
    SymbolReference, ValueExpression, MultiplicityExpression,
)
from graph_query_analysis.model import AnalysisRequest, BagExpression, Binding, SymbolicState
from graph_query_analysis.algebraic_translation import format_predicate, format_value, format_multiplicity, symbolic_trace
from graph_query_analysis.transforms import TransformError, UnsupportedCypherError

Certainty = Literal["entailed", "possible", "unknown"]
Level = Literal["information", "attention", "limitation", "error"]


@dataclass(frozen=True)
class Finding:
    """A human-readable conclusion with a predictable source and certainty.

    'possible' is deliberately not called an error: query intent and graph
    contents are unknown. Rules only explain the provided symbolic operators.
    """

    rule_id: str
    clause_index: int | None
    level: Level
    certainty: Certainty
    title: str
    explanation: str
    evidence: str = ""


@dataclass(frozen=True)
class ClauseReport:
    index: int
    clause: str
    scope_before: tuple[str, ...]
    scope_after: tuple[str, ...]
    introduced: tuple[str, ...]
    discarded: tuple[str, ...]
    rebound: tuple[str, ...]
    typed_bindings: tuple[str, ...]
    operations: tuple[str, ...]
    details: tuple[str, ...]
    findings: tuple[Finding, ...]
    formula_expression: MultiplicityExpression | None
    ordering: str
    limitation: str | None
    graph_pattern_nodes: int
    graph_pattern_relationships: int

    @cached_property
    def formula(self) -> str | None:
        """Render the mathematical appendix only on demand."""
        return (format_multiplicity(self.formula_expression)
                if self.formula_expression is not None else None)


@dataclass(frozen=True)
class QueryReport:
    query_text: str
    dialect: str
    schema_completeness: str
    status: Literal["compiled", "compilation_failed"]
    clauses: tuple[ClauseReport, ...]
    findings: tuple[Finding, ...]
    final_scope: tuple[str, ...]
    final_formula_expression: MultiplicityExpression | None
    compilation_error: str | None = None
    schema_details: tuple[str, ...] = ()
    parameter_names: tuple[str, ...] = ()
    semantic_sections: tuple["Section", ...] = ()
    semantic_fingerprint: tuple[str, ...] = ()
    priority_insights: tuple["Insight", ...] = ()

    @cached_property
    def final_formula(self) -> str | None:
        return (format_multiplicity(self.final_formula_expression)
                if self.final_formula_expression is not None else None)

    def render(self, *, show_formulas: bool = True,
               max_formula_chars: int | None = 3500) -> str:
        """Return an extensive plain-text report; no JSON export required."""
        if self.semantic_sections:
            from graph_query_analysis.reporting.render import render as render_compositional
            return render_compositional(self, show_formulas=show_formulas,
                                        max_formula_chars=max_formula_chars)
        return render_report(self, show_formulas=show_formulas,
                             max_formula_chars=max_formula_chars)


def _finding(rule: str, index: int | None, title: str, explanation: str,
             *, level: Level = "information", certainty: Certainty = "entailed",
             evidence: str = "") -> Finding:
    return Finding(rule, index, level, certainty, title, explanation, evidence)


def _name(symbol) -> str:
    # NUL is reserved by the existing transformer for anonymous/internal slots.
    return symbol.name.replace("\x00", "$internal:")


def _value(value: ValueExpression) -> str:
    if isinstance(value, ParameterReference):
        return "$" + value.name
    if isinstance(value, AggregateCall):
        argument = "*" if value.argument is None else _value(value.argument)
        return f"{value.name.upper()}({'DISTINCT ' if value.distinct else ''}{argument})"
    return format_value(value).replace("\x00", "$internal:")


def _pred(value: Predicate) -> str:
    return format_predicate(value)


def _columns(columns: tuple[Projection, ...]) -> tuple[str, ...]:
    result = []
    for i, column in enumerate(columns, 1):
        heading = column.alias or f"unnamed column {i}"
        kind = str(column.value_type) if column.value_type is not None else "unknown type"
        result.append(f"{heading} <= {_value(column.expression)} [{kind}]"
                      f"; output slot {_name(column.symbol)}")
    return tuple(result)


def _pattern(op: MatchBag) -> list[str]:
    details = []
    for node in op.nodes:
        labels = ":".join(node.labels)
        label_description = f"requires :{labels}" if labels else "no label restriction"
        details.append(f"Node slot {_name(node.symbol)}: {label_description}.")
        for predicate in node.predicates:
            details.append(f"  Inline node/property-map condition: {_pred(predicate)}")
    for rel in op.relationships:
        types = "|".join(rel.types) if rel.types else "any type"
        left, right, name = map(_name, (rel.left, rel.right, rel.symbol))
        if rel.direction == "INCOMING":
            shape = f"({left})<-[{name}:{types}]-({right})"
        elif rel.direction == "BOTH":
            shape = f"({left})-[{name}:{types}]-({right})"
        else:
            shape = f"({left})-[{name}:{types}]->({right})"
        details.append(f"Relationship witness {shape}.")
        for predicate in rel.predicates:
            details.append(f"  Inline relationship/property-map condition: {_pred(predicate)}")
    return details


def _operations_since(old: BagExpression, new: BagExpression) -> tuple[BagExpression, ...]:
    """Extract only newly added operators using immutable source identity.

    Never inspect the entire accumulated bag as if all its MATCH patterns
    were introduced by the current clause.
    """
    operators = []
    cursor = new
    while cursor is not old:
        operators.append(cursor)
        source = getattr(cursor, "source", None)
        if source is None:
            raise ValueError("State bag does not extend the preceding state bag")
        cursor = source
    operators.reverse()
    return tuple(operators)


def _has_ancestor(bag: BagExpression, kind: type) -> bool:
    cursor = bag
    while cursor is not None:
        if isinstance(cursor, kind):
            return True
        cursor = getattr(cursor, "source", None)
    return False


def _describe_operator(op: BagExpression, index: int) -> tuple[list[str], list[Finding]]:
    """Report guarantees or conditional implications of one operator only."""
    details: list[str] = []
    findings: list[Finding] = []

    if isinstance(op, MatchBag):
        details.extend(_pattern(op))
        details.append("This MATCH extends each incoming binding with every compatible "
                       "pattern witness; it is not a database lookup performed by the analyzer.")
        findings.append(_finding(
            "MATCH-WITNESSES", index, "Pattern witnesses affect row multiplicities",
            "A mandatory MATCH retains incoming rows only if the pattern can be matched. "
            "More than one compatible witness can produce multiple occurrences of the same "
            "later-projected row.",
            certainty="possible", level="attention",
            evidence=f"{len(op.nodes)} node requirements; {len(op.relationships)} relationship occurrences"))
        if len(op.relationships) > 1:
            findings.append(_finding(
                "MATCH-RELATIONSHIP-UNIQUENESS", index,
                "Relationships must be distinct within this MATCH",
                "Different relationship occurrences inside this MATCH must bind to distinct "
                "relationship identities under the modeled Cypher 25 semantics. The restriction "
                "is not automatically carried across separate MATCH clauses.",
                evidence=f"{len(op.relationships)} relationship occurrences in one MATCH"))
        if any(rel.direction == "BOTH" for rel in op.relationships):
            findings.append(_finding(
                "MATCH-UNDIRECTED", index, "Undirected relationship matching",
                "An undirected relationship pattern permits either stored orientation; a "
                "self-loop is not counted twice merely because it can be viewed both ways."))
        if any(node.predicates for node in op.nodes) or any(rel.predicates for rel in op.relationships):
            findings.append(_finding(
                "MATCH-INLINE-CONDITIONS", index, "Pattern-local conditions",
                "Inline WHERE/property-map conditions are attached to this MATCH's "
                "pattern requirements, rather than silently applied to all earlier patterns."))
        return details, findings

    if isinstance(op, FilterBag):
        details.append(f"WHERE condition: {_pred(op.predicate)}")
        details.append("Only rows for which the predicate evaluates to Cypher TRUE survive. "
                       "FALSE and NULL both cause the row to be removed.")
        findings.append(_finding(
            "FILTER-THREE-VALUED", index, "WHERE uses three-valued logic",
            "Filtering can discard rows but cannot increase their multiplicities. A NULL "
            "predicate result does not pass the filter.",
            evidence=_pred(op.predicate)))
        return details, findings

    if isinstance(op, ExtendBag):
        details.append("Computed projections are evaluated once while earlier input "
                       "bindings remain available to a subsequent WHERE/ORDER BY.")
        details.extend("  " + col for col in _columns(op.columns))
        details.append("The later ProjectBag removes input bindings that are not exported.")
        return details, findings

    if isinstance(op, ProjectBag):
        details.append("Projected output columns (in order):")
        details.extend("  " + col for col in _columns(op.columns))
        details.append("Rows producing identical output tuples have their multiplicities "
                       "added. The names and identities of discarded input variables are not "
                       "retained merely because they remain in the pattern snapshot.")
        findings.append(_finding(
            "PROJECTION-BAG", index, "Projection preserves duplicate occurrences",
            "Projection is not DISTINCT. If different input bindings produce the same "
            "projected values, the output row appears with their combined multiplicity.",
            certainty="possible", level="attention",
            evidence=f"{len(op.columns)} selected column(s)"))
        if _has_ancestor(op.source, DistinctBag) and any(
            not isinstance(col.expression, SymbolReference)
            or col.expression.symbol != col.symbol for col in op.columns
        ):
            findings.append(_finding(
                "PROJECTION-AFTER-DISTINCT", index,
                "A later projection can produce duplicates after an earlier DISTINCT",
                "Distinctness of earlier tuples does not imply distinctness after computing "
                "different values, such as projecting a property shared by multiple nodes. "
                "This is a possibility, not proof that duplicates exist.",
                certainty="possible", level="attention"))
        return details, findings

    if isinstance(op, DistinctBag):
        details.append("Output tuple multiplicity becomes support(m): 1 if m > 0, "
                       "otherwise 0. This operates on the current projected columns.")
        findings.append(_finding(
            "DISTINCT-SUPPORT", index, "DISTINCT deduplicates current output tuples",
            "Every existing distinct projected tuple occurs once after this operator. "
            "This does not ensure that a later projection of fewer/different columns "
            "will remain distinct.", evidence="Support(input multiplicity)"))
        return details, findings

    if isinstance(op, AggregateBag):
        if op.grouping_keys:
            details.append("Grouping keys (evaluated on input rows):")
            details.extend(f"  {_name(k.symbol)} <= {_value(k.expression)}"
                           for k in op.grouping_keys)
            details.append("Each distinct combination of grouping values forms a group "
                           "if at least one input row has those values. Grouping equality "
                           "treats NULL grouping values as equal.")
        else:
            details.append("Global aggregation: a single aggregate group is produced "
                           "even when the input bag is empty.")
        if op.aggregate_computations:
            details.append("Aggregate computations:")
            for comp in op.aggregate_computations:
                details.append(f"  {_name(comp.symbol)} <= {_value(comp.expression)}")
                call = comp.expression
                if call.name == "count":
                    details.append("    COUNT(*) counts occurrences; COUNT(expr) counts "
                                   "non-NULL expr values; DISTINCT removes duplicate arguments."
                                   if call.argument is not None else
                                   "    COUNT(*) counts input row occurrences, including duplicates.")
                elif call.name in {"sum", "avg", "min", "max"}:
                    details.append("    NULL arguments are omitted; values depend on actual "
                                   "group members and their multiplicities.")
                elif call.name == "collect":
                    details.append("    COLLECT ignores NULL inputs; its list order is "
                                   "not derived from unordered bag multiplicities.")
                if call.distinct:
                    details.append("    DISTINCT is applied to aggregate arguments, "
                                   "not necessarily to the final output rows.")
        details.append("Post-aggregation output columns:")
        details.extend("  " + col for col in _columns(op.columns))
        findings.append(_finding(
            "AGGREGATION-GROUPING", index, "Aggregation changes row granularity",
            "Output rows correspond to aggregate groups rather than individual "
            "pattern witnesses. A global aggregate and a grouped aggregate have different "
            "behavior on empty input.",
            evidence=f"{len(op.grouping_keys)} keys; {len(op.aggregate_computations)} aggregates"))
        if any(c.expression.name == "collect" for c in op.aggregate_computations):
            findings.append(_finding(
                "AGGREGATION-COLLECT-ORDER", index, "COLLECT requires list-order semantics",
                "A bag-level multiplicity formula does not determine the order of elements "
                "in COLLECT's list.", level="limitation", certainty="unknown"))
        return details, findings

    if isinstance(op, SortBag):
        details.append("ORDER BY keys (evaluated according to the compiled clause):")
        for key in op.keys:
            details.append(f"  {_value(key.expression)} {'DESC' if key.descending else 'ASC'}")
        details.append("Sorting changes row order, not bag multiplicity. Rows equal on all "
                       "sort keys may have unspecified relative order.")
        findings.append(_finding(
            "ORDER-TIES", index, "Equal sort keys do not ensure a unique order",
            "ORDER BY establishes key ordering but does not necessarily determine "
            "a total order for tied rows. This matters for pagination.",
            certainty="possible", level="attention"))
        return details, findings

    if isinstance(op, SliceBag):
        skip = _value(op.skip) if op.skip is not None else "none"
        limit = _value(op.limit) if op.limit is not None else "none"
        details.append(f"Stream slicing: SKIP={skip}; LIMIT={limit}.")
        details.append("SKIP/LIMIT select row occurrences from a stream. An unordered "
                       "bag expression is insufficient to identify exactly which rows survive.")
        findings.append(_finding(
            "SLICE-STREAM", index, "Pagination needs ordered-stream semantics",
            "The current symbolic bag engine does not derive the exact multiplicity "
            "formula after SKIP/LIMIT. The report records the operator without "
            "inventing a deterministic selection.", level="limitation", certainty="unknown"))
        if not _has_ancestor(op.source, SortBag):
            findings.append(_finding(
                "SLICE-NO-ORDER", index, "Pagination has no explicit ordering",
                "Without an ORDER BY in the relevant input stream, which rows are "
                "selected is not guaranteed. This does not imply the query is invalid.",
                level="attention", certainty="possible"))
        return details, findings

    details.append(f"Operator {type(op).__name__}: no report-specific rule is registered.")
    findings.append(_finding(
        "REPORT-UNRECOGNIZED-OPERATOR", index, "Operator not described by reporter",
        "The compiled operator exists but the rule-based reporter has no specialized "
        "explanation. No semantic conclusion is inferred for it.",
        level="limitation", certainty="unknown"))
    return details, findings


def _schema_details(request: AnalysisRequest) -> tuple[str, ...]:
    """Only supplied structural metadata; no inferred data existence."""
    schema = request.schema
    details: list[str] = []
    for label, properties in sorted(schema.node_properties.items()):
        prop_text = ", ".join(f"{name}: {kind}" for name, kind in sorted(properties.items()))
        details.append(f"Node label {label}: {prop_text or '(no observed properties listed)'}")
    for signature in schema.relationships:
        details.append(f"Relationship signature ({signature.start_label})-"
                       f"[:{signature.relationship_type}]->({signature.end_label})")
    for rel_type, properties in sorted(schema.relationship_properties.items()):
        prop_text = ", ".join(f"{name}: {kind}" for name, kind in sorted(properties.items()))
        details.append(f"Relationship type {rel_type}: "
                       f"{prop_text or '(no observed properties listed)'}")
    return tuple(details)


def _binding_changes(before: Sequence[Binding], after: Sequence[Binding]
                     ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    old = {b.variable: b for b in before}
    new = {b.variable: b for b in after}
    added = tuple(n for n in new if n not in old)
    removed = tuple(n for n in old if n not in new)
    rebound = tuple(n for n in new if n in old and new[n].symbol != old[n].symbol)
    return added, removed, rebound


def _typed_bindings(state: SymbolicState) -> tuple[str, ...]:
    return tuple(f"{b.variable}: {str(b.value_type) if b.value_type is not None else b.value_kind} "
                 f"[slot {_name(b.symbol)}]" for b in state.scope)


def generate_report(request: AnalysisRequest,
                    states: Sequence[SymbolicState] | None = None) -> QueryReport:
    """Build one extensive rule-based report from an already-parsed request.

    Pass `states` when already compiled to avoid compiling twice. With no states,
    a compilation failure is returned as an explicit report error, not guessed
    from an incomplete AST. No contrast to natural-language intent is attempted.
    """
    if states is None:
        try:
            states, failed_states = compile_request(request)
        except (TransformError, UnsupportedCypherError) as exc:
            message = str(exc)
            finding = _finding(
                "COMPILATION-FAILED", None, "Query was not compiled",
                "The current compiler rejected the query. This may be a scope/type "
                "error or syntax outside the supported fragment; it is not automatically "
                "proof that Neo4j itself rejects the query.",
                level="error", certainty="entailed", evidence=message)
            return QueryReport(request.query_text, request.dialect,
                               request.schema.completeness, "compilation_failed",
                               (), (finding,), (), None, message,
                               _schema_details(request), tuple(sorted(request.parameters)))

    if len(states) != len(request.clauses) + 1:
        raise ValueError("Expected initial state and exactly one state per clause")

    trace = symbolic_trace(states, request.clauses, format_formulas=False)
    global_findings = [
        _finding("SCHEMA-BOUNDARY", None, "Graph-independent interpretation",
                 "All formulas and pattern slots describe potential results over a "
                 "compatible finite graph. The report does not establish which "
                 "nodes or relationships exist, or whether the query matches the "
                 "original natural-language intent.",
                 evidence=f"schema_completeness={request.schema.completeness}"),
    ]
    if request.schema.completeness == "observed":
        global_findings.append(_finding(
            "SCHEMA-OBSERVED", None, "Observed schema is not exhaustive",
            "Missing labels, relationship signatures, or property descriptions cannot "
            "be treated as proof that a match or property is impossible.",
            certainty="unknown", level="limitation"))
    else:
        global_findings.append(_finding(
            "SCHEMA-EXHAUSTIVE", None, "Caller-declared exhaustive schema",
            "Completeness refers to the supplied schema inventory; it does not "
            "by itself assert that entities or required property values exist."))

    sections: list[ClauseReport] = []
    all_findings: list[Finding] = list(global_findings)
    for index, clause in enumerate(request.clauses, 1):
        before, after = states[index - 1], states[index]
        operators = _operations_since(before.bag, after.bag)
        details: list[str] = []
        local: list[Finding] = []
        added, removed, rebound = _binding_changes(before.scope, after.scope)
        if added:
            details.append("Newly visible variables: " + ", ".join(added) + ".")
        if removed:
            details.append("Variables removed from scope: " + ", ".join(removed) + ".")
            local.append(_finding(
                "SCOPE-DISCARDED", index, "Variables leave scope",
                "The discarded variable names cannot be referenced in subsequent clauses "
                "unless they are introduced again. Their earlier pattern requirements "
                "remain part of the bag's history.",
                evidence=", ".join(removed)))
        if rebound:
            details.append("Names rebound to new symbolic slots: " + ", ".join(rebound) + ".")
            local.append(_finding(
                "SCOPE-REBOUND", index, "A visible name now denotes a different slot",
                "The same Cypher variable name is associated with a new computed symbolic "
                "slot. This is not an assertion of a different underlying graph entity.",
                evidence=", ".join(rebound)))
        if not after.scope:
            details.append("No named variables are exported by this state.")
        for operator in operators:
            operator_details, operator_findings = _describe_operator(operator, index)
            details.extend(operator_details)
            local.extend(operator_findings)

        symbolic = trace[index]
        if symbolic.limitation:
            local.append(_finding(
                "SYMBOLIC-UNSUPPORTED", index, "Exact symbolic formula unavailable",
                "The semantic interpreter deliberately declined to derive an exact "
                "formula for this state. Previous completed states remain interpretable.",
                level="limitation", certainty="unknown", evidence=symbolic.limitation))
        graph = after.graph.graph
        sections.append(ClauseReport(
            index=index, clause=clause["type"],
            scope_before=tuple(b.variable for b in before.scope),
            scope_after=tuple(b.variable for b in after.scope),
            introduced=added, discarded=removed, rebound=rebound,
            typed_bindings=_typed_bindings(after),
            operations=tuple(type(o).__name__ for o in operators),
            details=tuple(details), findings=tuple(local),
            formula_expression=symbolic.formula, ordering=symbolic.ordering,
            limitation=symbolic.limitation,
            graph_pattern_nodes=graph.number_of_nodes(),
            graph_pattern_relationships=graph.number_of_edges()))
        all_findings.extend(local)

    basic = QueryReport(request.query_text, request.dialect,
                       request.schema.completeness, "compiled", tuple(sections),
                       tuple(all_findings), sections[-1].scope_after if sections else (),
                        trace[-1].formula, None,
                       _schema_details(request), tuple(sorted(request.parameters)))
    from graph_query_analysis.reporting.core import AnalysisContext
    from graph_query_analysis.reporting.synthesis import create_sections, fingerprint, prioritize
    context = AnalysisContext(request, states)
    semantic_sections = create_sections(context)
    return replace(basic, semantic_sections=semantic_sections,
                   semantic_fingerprint=fingerprint(context),
                   priority_insights=prioritize(semantic_sections))


def render_report(report: QueryReport, *, show_formulas: bool = True,
                  max_formula_chars: int | None = 3500) -> str:
    """Readable text for a developer or an LLM; no serialization dependency."""
    lines: list[str] = []

    def heading(title: str) -> None:
        lines.extend(("", title, "-" * len(title)))

    lines.extend(("SCHEMA-ONLY CYPHER ANALYSIS REPORT", "=" * 34,
                  f"Dialect: {report.dialect}",
                  f"Schema: {report.schema_completeness}",
                  f"Compilation: {report.status}",
                  "Database records accessed: none", "Query:", report.query_text))
    if report.compilation_error is not None:
        heading("Compilation failure")
        lines.append(report.compilation_error)
    heading("Supplied schema context")
    lines.append("Only schema descriptions are shown, not graph records or "
                 "existence/uniqueness guarantees.")
    if report.schema_details:
        lines.extend("  " + line for line in report.schema_details)
    else:
        lines.append("  (no structural schema entries supplied)")
    lines.append("Parameter names: " + (", ".join("$" + n for n in report.parameter_names) or "(none supplied)"))
    heading("Interpretation boundary")
    lines.append("The report explains compiled Cypher behavior, not query correctness "
                 "relative to a user's question. 'Possible' effects are not established "
                 "database facts. No counts or entity identities are fabricated.")
    if report.status == "compiled":
        heading("Query summary")
        lines.extend((f"Clauses: {len(report.clauses)}",
                      "Final visible columns: " + (", ".join(report.final_scope) or "(none)"),
                      "Clause/operator pipeline:"))
        for c in report.clauses:
            lines.append(f"  {c.index}. {c.clause}: " +
                         (" -> ".join(c.operations) if c.operations else "(no new operator)"))
    heading("Findings by level")
    for level in ("error", "limitation", "attention", "information"):
        selected = [f for f in report.findings if f.level == level]
        lines.append(f"{level.upper()}: {len(selected)}")
        for finding in selected:
            origin = "query" if finding.clause_index is None else f"clause {finding.clause_index}"
            lines.append(f"  [{finding.rule_id}] ({origin}; {finding.certainty}) {finding.title}")
            lines.append(f"    {finding.explanation}")
            if finding.evidence:
                lines.append(f"    Evidence: {finding.evidence}")
    if report.status == "compiled":
        heading("Clause-by-clause interpretation")
        for c in report.clauses:
            lines.append("")
            lines.append(f"CLAUSE {c.index}: {c.clause.upper()}")
            lines.append("~" * (10 + len(str(c.index)) + len(c.clause)))
            lines.append("Scope before: " + (", ".join(c.scope_before) or "(empty)"))
            lines.append("Scope after:  " + (", ".join(c.scope_after) or "(empty)"))
            lines.append("Introduced: " + (", ".join(c.introduced) or "none"))
            lines.append("Discarded:  " + (", ".join(c.discarded) or "none"))
            lines.append("Rebound:    " + (", ".join(c.rebound) or "none"))
            lines.append("Visible binding types:")
            if c.typed_bindings:
                lines.extend("  " + line for line in c.typed_bindings)
            else:
                lines.append("  (none)")
            lines.append("Applied operations: " + " -> ".join(c.operations))
            lines.append("Explanation:")
            lines.extend("  " + detail for detail in c.details)
            lines.append("Accumulated *pattern snapshot* (not concrete graph): "
                         f"{c.graph_pattern_nodes} node slots, "
                         f"{c.graph_pattern_relationships} relationship occurrences")
            lines.append("Ordering: " + c.ordering)
            if show_formulas:
                if c.formula is None:
                    lines.append("Symbolic multiplicity: not available")
                else:
                    formula = c.formula
                    if max_formula_chars is not None and len(formula) > max_formula_chars:
                        lines.append("Symbolic multiplicity (display truncated; expression "
                                     "retained in report object):")
                        formula = formula[:max_formula_chars] + " ... [truncated]"
                    else:
                        lines.append("Symbolic multiplicity:")
                    lines.append("  " + formula)
            if c.limitation:
                lines.append("Semantic limitation: " + c.limitation)
        heading("Final interpretation")
        if report.final_formula_expression is None:
            lines.append("An exact final bag multiplicity formula is unavailable for "
                         "this supported operator chain; consult the listed limitations.")
        else:
            lines.append("The final formula describes each possible output row's "
                         "multiplicity over an arbitrary compatible finite graph; "
                         "it does not assert actual result rows or counts.")
    return "\n".join(lines).strip() + "\n"


def get_reports(
    requests_path: str | Path,
    show_formulas: bool = True,
    output_path: str | Path | None = None,
) -> str:
    from graph_query_analysis.input import load_requests

    requests = load_requests(requests_path)

    reports = [
        generate_report(request).render(show_formulas=show_formulas)
        for request in requests
    ]

    result = "\n\n".join(reports)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding="utf-8")

    return result


if __name__ == "__main__":
    result = get_reports(
        requests_path="graph_analysis_requests/cypherast_lean_request.json",
        show_formulas=False,
        output_path="reports/analysis.txt",
    )
