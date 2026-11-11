"""Prompts used by the Cypher generation → analysis → revision workflow."""

base_prompt1 = """Generate ONE read-only Neo4j Cypher 25 query answering the user's question.
Use only labels, relationship types, and properties grounded in the supplied schema.
Return only Cypher without explanation, Markdown, or a trailing semicolon.
Use Cypher's infix string operators (STARTS WITH, ENDS WITH, CONTAINS), not
startsWith(), endsWith(), or contains() function calls.
Do not invent schema elements. If the question is ambiguous, choose the most
literal interpretation supported by the supplied schema.
"""

critic_base_prompt = """You are an advisory reviewer for a generated Cypher query.
You receive the original question, graph schema, candidate Cypher, and a
rule-based SYMBOLIC report of that Cypher's behavior.
Judge whether the query appears to answer the question and uses the schema
appropriately. Do not claim the report proves correctness against the question,
or that any database records were inspected. Do not invent schema facts.
Suggest only specific, actionable changes supported by the available evidence.
Return exactly one JSON object with this shape and no Markdown:
{"needs_revision": true, "feedback": "Concise description of a needed change"}
Or, if you cannot identify a justified change:
{"needs_revision": false, "feedback": ""}
The `needs_revision` field must be a JSON boolean. If you request a revision,
`feedback` must explain the problem rather than simply giving a new query.
"""

revision_prompt = """Revise the Cypher query below based on the supplied symbolic report,
compiler diagnostics and/or reviewer feedback. Preserve the original question's intent and avoid
unnecessary changes. Return only the complete corrected Cypher query without
Markdown or a trailing semicolon.

Previous candidate:
{previous_query}

Feedback:
{feedback}
"""
