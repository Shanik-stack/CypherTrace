# LEAN-Cypher implementation progress

**Historical Lean work.** The active Python design and tested behavior are
documented in [python-plan.md](python-plan.md).

This tracks work against [plan.md](../plan.md), especially the phases in §14 and
the first symbolic query in §18. The analysis input remains a checked query from
the unified JSON request and its schema. Concrete graph fixtures are used to test
and prove the interpretation of the symbolic result.

## Phase 0 · Baseline

Lean and Lake build with Lean `v4.35.0`. The request dialect is `neo4j25`, Cypherast
is pinned at `0.1.12`, and the live fixture configuration uses `neo4j:2026.10.1`
with Cypher 5 explicitly selected. Repository-level CI builds the Lean package,
checks theorem axioms, and runs the two Python regression suites.

| Feature | Implemented and executable | Symbolic output | Current Lean coverage | Local test coverage |
| --- | --- | --- | --- | --- |
| Fixed-length mandatory `MATCH` | Yes | Witness extension | Match witnesses, relationship uniqueness, per-witness occurrence counts | Parallel edges, self-loops, directions, repeated bindings, multiple paths |
| Attached `WHERE` | Yes, supported expressions | Filter stage | Successful filters keep exactly the true occurrences. Closed blocking predicates have a separate theorem. | Null and numeric predicates, filters and error propagation |
| `WITH` and `RETURN`, with or without `DISTINCT` | Yes | Projection and deduplication stages | Scoped references, projection counts and first-representative deduplication | Aliases, invalid references, duplicate rows and stage-local `DISTINCT` |
| Schema catalog and explicit property assumptions | Yes, graph-free analysis | Schema context | Assumption satisfaction has a Lean definition. The current symbolic proofs quantify over all validated graphs. | Catalog evidence, explicit assumptions and invalid assumption rejection |
| Standalone aliased `AVG` | Analysis only | Input bag | Coverage ends at its input stages | AST decoding, argument type evidence, and symbolic input boundary |

The local Lean build, axiom audit of 29 theorems, and 25 targeted Python tests
pass. Two live Neo4j tests require a running instance and were skipped. For the
live setup, see the [Lean README](../lean/README.md).

## Phase 1 · Library compatibility

| Candidate | Exact source | Current decision |
| --- | --- | --- |
| Mathlib | `v4.35.0` at `56a005855ea17e0b0958f73ec801ae140b0a1679` | Direct dependency. `BagSemantics` uses `Multiset.filterMap` for the unordered projection bag, `filterMap_add` for its additivity, and multiset counts for the projection multiplicity theorem. The [spike](../lean/compat/MathlibSpike.lean) compiles parallel arrows, relational support composition, and projection counts with `Multiset` and `Finsupp`. |
| `provenance-lean` | `v1.3.0` at `e6e95cdcd44fd6d2178c0d8a0d631a1170738011` | Reference for this increment. Its plain relational projection is `Multiset.map` and its deduplication uses tuple equality. Direct Mathlib reuse covers this bag law. A Cypher adapter would still have to bridge error-bearing projection, row comparison and representative selection. The library was assessed from the pinned source. |

The executable retains its ordered rows so it preserves the `DISTINCT`
representative and evaluation errors. The multiset law applies to projection
before `DISTINCT`. `Quiver`, `SetRel` and `Finsupp` are still limited to the
compatibility spike. To repeat the spike from `lean/`, fetch its imports and run
the proof file.

```text
lake exe cache get Mathlib.Basic.Rel Mathlib.Combinatorics.Quiver.Basic Mathlib.Data.Finsupp.Multiset Mathlib.Data.Multiset.Dedup
lake env lean compat/MathlibSpike.lean
```

## Phase 2 · Schema and graph model

Typed observations and explicit assumptions now travel separately through the
request. A declared or enforced required-property assumption constrains the
graphs quantified over by a future theorem. A catalog entry does not constrain
that domain. The assumption's source is caller-provided, and the report records
the supplied facts separately from any assumptions used by proofs.

For this slice the abstract graph variable ranges over the existing finite graph
model, with separate node and relationship identities. The compiler and renderer
take no graph records. A separately abstracted graph carrier and a stored-property
well-formedness predicate remain open. Introduce additional schema assumption
families as proofs need them, with exhaustiveness explicit.

The concrete baseline also leaves several conformance questions open. Expression
checking currently establishes scope before execution and still needs to account
for property-access types. Non-finite values, string predicates and Boolean
error propagation need explicit semantics or live differential cases. A malformed
request can still stop its batch.

## Phases 3–5 · First symbolic query

The checked plan compiles to a scope-indexed sequence of bag operators, starting
with the singleton empty row. `MATCH` extends witnesses, `WHERE` filters them,
projection sums the occurrences landing on each output, and `DISTINCT` retains
one representative under the model's row comparator. Anonymous relationships
have their own witness identities. Repeated variable names must bind to the same
value. Each `MATCH` has its own relationship freshness constraint.

The analysis JSON serializes these operators and their input bags. Lean proves
agreement with the reference evaluator, including errors, and exact counts for
every observation of a successful result. Lean also proves the zero-or-one
deduplication count for equivalence classes, conditional on the row comparator
being an equivalence relation. The conformance fixture exercises both
interpretations and obtains six occurrences with `WITH b` and three with
`WITH DISTINCT b`. The [saved symbolic example](../lean_outputs/symbolic_bag_analysis.json)
is produced from the [unified request](../lean_requests/symbolic_bag_request.json).

## Phase 6 · Certified emptiness checks

`MATCH` and `WHERE` stages expose necessary constraints. A single bounded checker
certifies a contradiction and the pipeline's empty-input law propagates the
result through later row stages. The [saved analysis](../lean_outputs/constraint_proof_analysis.json)
shows closed predicates, contradictory bounds and repeated relationship names
handled by the same stage theorem. The reports also include an unresolved
disjunction. Runtime analysis uses the symbolic goal path. `BlockingFilter`
remains available as a proof baseline.

This increment covers stage emptiness on success. Predicate translation handles
conjunctions of ordered comparisons against integer literals, including reversed
operands and comparison against a floating-point property. Other comparisons,
open disjunctions, cross-stage constraints, and broader goals such as count bounds
remain open. The solver uses `omega` in its generic arithmetic soundness proof
and runs bounded certified search over the extracted constraints at analysis time.

Later phases build broader findings, aggregation, optional matching and
equivalence on these transformations. Exact counts can remain symbolic when
the request only provides schema and AST.
