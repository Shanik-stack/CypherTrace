# Active plan: Python Cypher analysis

The Python implementation is now the active system. The older [plan](../plan.md)
records the previous Lean research direction for reference.

1. Parse the exact submitted Cypher, preserving its text, the supplied schema,
   and the serialized CypherAST together in one request.
2. Translate each supported clause to an operator on the preceding symbolic
   bag. Preserve node and relationship identities, witnesses, variable scope,
   row multiplicity, grouping, and NULL behavior. Reject unsupported semantics
   explicitly instead of silently inventing behavior.
3. Derive formulas and report findings from this same operator sequence.
   `COUNT(DISTINCT entity)` compares identities; `COUNT(DISTINCT entity.property)`
   compares property values. A claim about earlier deduplication requires a
   real preceding `DISTINCT` or grouping operator.
4. Keep orchestration as plain functions: one parser, one analyzer, one bounded
   revision loop, one per-run output writer. Use one reusable CypherAgent for
   generation and optional review; keep meaningful graph structures in the core.
5. Test generic transformations on varied finite graph fixtures and compare
   read-only constant queries with Neo4j when a test server is available.

## Current scope and limits

- Supported: fixed-length mandatory `MATCH`, the implemented value predicates,
  `WITH`, `RETURN`, projection, `DISTINCT`, grouping, `count`, `sum`, `avg`,
  `min`, `max`, `collect`, ordering and pagination in the implemented fragment.
  `OPTIONAL MATCH`, variable length paths, subqueries, writes and the full
  Neo4j value domain remain unsupported. Some supported operators are modeled
  only as partial or unknown symbolic formulas when order matters.
- The schema is an observed catalog unless explicitly marked exhaustive.
  An observed label/property/signature cannot assert that matching graph data
  exists or that a property is always present. Normalized relationship entries
  retain endpoint-specific properties, but static type inference still reads
  the broader relationship-type catalog conservatively.
- `entailed` in reports means implied by the current Python semantics; it is
  not a formal proof certificate. Exact symbolic `collect` ordering and
  selection after `SKIP`/`LIMIT` need ordered-stream reasoning.
- Files under `outputs/` are per-run histories and formulas. Concrete graph
  records are needed only by the optional test interpreter, never by analysis.

Next increments should add one missing Cypher transformation and meaningful
positive/negative fixture tests at a time. The correctness gate is that a
finding follows from general composed operators, without a detector written
for an individual benchmark question.
