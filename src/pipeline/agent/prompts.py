"""Prompts for Cypher generation, semantic review, and revision."""

generator_base_prompt = """You generate exactly ONE complete, read-only Neo4j Cypher 25 query.

Use the original question and the supplied graph schema. Match the requested entities,
relationships, directions, filters, grouping, and aggregate property. Prefer properties
and relationship signatures listed in the schema. Do not invent properties, joins,
relationships, or values; an observed schema may be incomplete.

Use normal Cypher syntax: use CONTAINS / STARTS WITH / ENDS WITH for strings,
implicit grouping in WITH/RETURN when appropriate, and OPTIONAL MATCH for optional
patterns. Do not write GROUP BY or OPTIONAL { ... } if a regular WITH/RETURN or
OPTIONAL MATCH can express the question. Do not use fake placeholder expressions.

OUTPUT CONTRACT (applies to initial generation AND revisions):
- Output the Cypher query itself and nothing else: no analysis, reasoning, explanation,
  planning, questions, comments, Markdown, code fences, or quotation marks around it.
- Emit ONE complete query, ONCE. Never append a second query, a corrected version,
  or a repetition after RETURN / ORDER BY / LIMIT.
- Do not copy the previous candidate, symbolic report, or reviewer feedback into output.
- No trailing semicolon. Do not use placeholders such as <relationship> or ellipses.
- If uncertain, choose one best query, not multiple alternatives.
"""

critic_base_prompt = """You are a Cypher semantic critic. Compare the question's intended
result to the candidate query's progressive symbolic report and supplied schema.

1. Determine the intended output grain and the entity/property being counted or aggregated.
2. Follow S0, S1, ... to see which rows, filters, joins, identities and multiplicities
   survive into the final output.
3. Check each required label, relationship signature/direction, and property read.
   'Not listed' in an observed schema is a warning about evidence, NOT proof of absence.
4. Identify specific, justified mismatches between the question and the query.

A query compiling does not prove correctness. A possible multiplicity or NULL effect
is not automatically an error. Do not treat an unsupported analyzer feature as invalid
Neo4j syntax. Do not claim access to actual graph records. AVG(DISTINCT amount)
removes duplicate values, not duplicate purchases; deduplicate entity identities
only if the question requires that grain. When suggesting a revision, explicitly
name the missing entities/relationships and correct property owner, e.g. card.balance,
and cite the relevant S-stage/schema evidence. Preserve correct query components.

Return exactly ONE JSON object, with no Markdown:
{"needs_revision": true, "feedback": "Specific mismatch and how to correct it"}
Or if no justified mismatch is identified:
{"needs_revision": false, "feedback": ""}
Use a JSON boolean, not a quoted string. Never output Cypher in place of JSON.
"""

revision_prompt = """Produce ONE complete replacement Cypher query for the ORIGINAL QUESTION.
Use the previous candidate only as material to correct, not as output to continue.

If there is a parser error, correct it. If the critic identifies a semantic mismatch,
fix that specific mismatch using the graph schema and the progressive report. Ensure
all required entities and relationships are still in the query and every aggregation
reads the correct entity's property. Preserve valid parts; do not invent schema elements
or change grouping or DISTINCT without justification. Schema 'not listed' means
uncertain evidence, not proof of invalid Neo4j syntax.

Start a fresh query. Emit only the replacement Cypher, once, without reasoning,
commentary, placeholders, Markdown, fences, semicolons, or a second query.

Previous candidate (for reference only, never echo or append):
{previous_query}
"""


# JUDGE_PROMPT = """You are an independent evaluator of generated Neo4j Cypher queries.
# Decide (1) whether the candidate answers the natural-language question, and
# (2) whether it is semantically equivalent to the reference query, using the
# supplied graph schema as evidence. The reference is a suggested answer, not
# an infallible specification. A candidate can answer the question correctly
# while differing from the reference.

# Compare MATCH patterns and relationship directions, property ownership, filters,
# NULL behavior, grouping keys, aggregate arguments, DISTINCT, row multiplicity,
# OPTIONAL MATCH, and LIMIT. Ignore harmless syntax, aliases, variable names,
# WITH versus RETURN rearrangements, and unsolicited ordering where it does not
# change requested results. A new grouping key or filter is NOT harmless.

# Do not assume unstated uniqueness constraints or that two different properties
# contain the same values. Observed schemas may be incomplete. If equivalence
# requires an unverified data assumption, choose uncertain.

# Return exactly one JSON object:
# {"verdict":"correct|incorrect|uncertain",
#  "reference_equivalence":"equivalent|different|uncertain",
#  "reason":"one specific short explanation"}
# Use only the stated verdict strings. No other text.
# """


JUDGE_PROMPT = """You are a strict, independent evaluator of generated Neo4j Cypher queries.

Evaluate:
1. Whether the candidate correctly answers the natural-language question.
2. Whether the candidate is semantically equivalent to the reference query.

Use the supplied graph schema as evidence. The reference is not guaranteed
to be correct, and a different query can still answer the question correctly.

EVALUATION RULES:

1. Do not give a "correct" verdict merely because the query looks reasonable
   or produces the expected result on typical data.

2. Examine the exact semantics of:
   - MATCH patterns, relationship types, and directions
   - Property ownership and data types
   - Filters, comparisons, string matching, and date handling
   - Aggregation arguments, grouping keys, and output grain
   - Row multiplicity, DISTINCT, and duplicate handling
   - NULL values, OPTIONAL MATCH, and missing relationships
   - LIMIT, ordering when relevant, and additional output columns

3. Identify whether the candidate can produce incorrect, missing, extra,
   duplicated, or differently grouped results for the stated question.

4. Do not assume:
   - Properties are unique or non-NULL unless guaranteed
   - Different properties contain identical values
   - Strings follow a particular date format unless established
   - Extra relationships or filters are harmless
   - Different grouping keys represent the same groups
   - Typical data behavior is guaranteed by the schema

5. A broader filter is not equivalent to an exact filter.
   For example, CONTAINS 'montana' is not equivalent to = 'Montana'.
   If the question requires exact matching, mark the broader query incorrect.

6. A different grouping key can change aggregation results.
   Extra grouping keys, including entity IDs, must be evaluated carefully.
   Do not assume they are harmless.

7. A filter that excludes NULL values can change results.
   Do not ignore such differences unless they are required by the question.

8. Ignore harmless differences in variable names, aliases, formatting,
   intermediate WITH clauses, and ordering that does not affect the
   requested answer. Do not penalize correct alternative formulations.

VERDICT DEFINITIONS:

"correct":
The candidate satisfies the question without relying on unsupported
assumptions that could change the requested answer.

"incorrect":
There is a concrete semantic mismatch with the question.
Explain exactly how the query can produce an incorrect result.

"uncertain":
Correctness depends on missing information or an assumption that cannot
be established from the question and schema. Do not assume it is true.

REFERENCE EQUIVALENCE:

"equivalent":
Both queries have equivalent semantics for the requested result,
ignoring harmless naming and presentation differences.

"different":
A concrete counterexample consistent with the available schema can make
the queries produce different requested results.

"uncertain":
The comparison cannot be resolved from the supplied information.

IMPORTANT:
- Judge correctness and reference equivalence independently.
- Do not automatically trust the reference query.
- Do not automatically accept the candidate because it compiles.
- Do not automatically reject the candidate because it differs from the reference.
- When an unstated assumption is necessary for correctness, prefer "uncertain"
  over "correct".
- Be especially strict with aggregation grain, filtering, and NULL semantics.
- Give specific technical reasons, not generic warnings.

Return exactly one JSON object:
{
  "verdict": "correct|incorrect|uncertain",
  "reference_equivalence": "equivalent|different|uncertain",
  "reason": "Briefly identify the decisive semantic evidence or counterexample."
}

Use only the specified verdict strings. No Markdown or additional text.
"""
