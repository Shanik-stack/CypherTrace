"""Deterministic plain-text presentation with a technical symbolic appendix."""
from graph_query_analysis.reporting.core import Insight


def render(report, *, show_formulas: bool = True, max_formula_chars: int | None = 3500) -> str:
    lines: list[str] = []

    def section(title: str) -> None:
        lines.extend(['', title, '=' * len(title)])

    lines.extend(['COMPOSITIONAL SEMANTIC EXPLANATION — CYPHER 25',
                  '=' * 48, f'Compilation: {report.status}',
                  f'Schema completeness: {report.schema_completeness}',
                  'Graph records accessed: none', 'Query:', report.query_text])
    section('1. Query semantic fingerprint')
    for line in report.semantic_fingerprint:
        lines.append('  ' + line)
    section('2. Most consequential query-specific findings')
    if not report.priority_insights:
        lines.append('  No additional cross-clause insight is established for this supported fragment.')
    for n, insight in enumerate(report.priority_insights, 1):
        clause = f'clause {insight.clause}' if insight.clause is not None else 'whole query'
        lines.append(f'  {n}. [{insight.rule_id}] {insight.title}')
        lines.append(f'     Certainty: {insight.certainty}; importance: {insight.importance}; at {clause}')
        lines.append('     ' + insight.explanation)
        if insight.evidence:
            lines.append('     Evidence: ' + '; '.join(insight.evidence))
        if insight.intent_check:
            lines.append('     Intent check: ' + insight.intent_check)
    section('3. Detailed compositional analyses')
    for number, sec in enumerate(report.semantic_sections, 1):
        lines.append('')
        lines.append(f'3.{number} {sec.title}')
        lines.append('-' * (6 + len(sec.title)))
        lines.append(sec.overview)
        lines.extend('  ' + line for line in sec.details)
        if sec.insights:
            lines.append('  Derived findings:')
            for finding in sec.insights:
                lines.append(f'  - [{finding.rule_id} | {finding.certainty}] {finding.title}')
                lines.append('    ' + finding.explanation)
                if finding.evidence:
                    lines.append('    Evidence: ' + '; '.join(finding.evidence))
    section('4. Clause-by-clause operator transitions')
    for clause in report.clauses:
        lines.append(f'Clause {clause.index} — {clause.clause}')
        lines.append('  Operators: ' + ' -> '.join(clause.operations))
        lines.append('  Before: ' + (', '.join(clause.scope_before) or '(none)'))
        lines.append('  After: ' + (', '.join(clause.scope_after) or '(none)'))
        if clause.discarded:
            lines.append('  Discarded from visible scope: ' + ', '.join(clause.discarded))
        if clause.rebound:
            lines.append('  Names bound to different slots: ' + ', '.join(clause.rebound))
        if clause.typed_bindings:
            lines.append('  Types: ' + '; '.join(clause.typed_bindings))
        if clause.limitation:
            lines.append('  Semantic limitation: ' + clause.limitation)
    section('5. Mathematical multiplicity appendix')
    if show_formulas:
        for clause in report.clauses:
            lines.append(f'Clause {clause.index} ({clause.clause}):')
            if clause.formula is None:
                lines.append('  No exact bag formula for this stage.')
            else:
                formula = clause.formula
                if max_formula_chars is not None and len(formula) > max_formula_chars:
                    formula = formula[:max_formula_chars] + ' ... [display truncated]'
                lines.append('  ' + formula)
    else:
        lines.append('  Omitted by caller; underlying expressions remain in symbolic states.')
    section('6. Interpretation boundaries and remaining capabilities')
    lines.extend([
        '  - This reports properties of the compiled query, not intent correctness.',
        '  - CONDITIONAL findings describe behavior on some compatible graphs, not observed data.',
        '  - UNKNOWN schema facts cannot justify claims about actual entity existence.',
        '  - No contradiction solver, query equivalence prover or database execution is used.',
        '  - OPTIONAL MATCH, variable-length patterns, subqueries and writes are not modeled here.',
        '  - Exact SKIP/LIMIT and ordered COLLECT outputs require richer stream semantics.',
        '  - Do not infer specific repair steps unless the natural-language request supports them.',
    ])
    if report.schema_details:
        section('7. Supplied schema inventory (reference)')
        lines.extend('  ' + x for x in report.schema_details)
    return '\n'.join(lines).rstrip() + '\n'
