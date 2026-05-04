# Candidate Scoring Criteria

Each surviving function (post atomicity filter) is scored on the following
axes. Each axis is rated 0–1; the combined score is a weighted average.

## Axes

### 1. Self-containedness  (weight 0.20)
- 1.0: function + its helpers live in one file; no project-internal imports
  beyond stdlib / well-known third-party libs.
- 0.5: relies on a couple of helpers from the same package that could be
  inlined cheaply.
- 0.0: deeply tied to package internals; copying it into an isolated
  `initial.py` would require dozens of supporting symbols.

### 2. Determinism  (weight 0.15)
- 1.0: pure function or RNG-seeded; two runs on the same inputs produce the
  same output.
- 0.5: depends on wall-clock or unseeded RNG but can be made deterministic
  with a small wrapper.
- 0.0: depends on external state (network, threads, cache files) that can't
  be reasonably pinned.

### 3. Numeric / measurable output  (weight 0.20)
- 1.0: returns or implies an obvious scalar metric (loss, accuracy,
  runtime, count, ratio, distance, score).
- 0.5: output is structured and a metric can be derived with a small
  scoring helper.
- 0.0: output is text/freeform with no obvious quantitative target.

### 4. Improvability surface  (weight 0.20)
- 1.0: substantial algorithmic body — loops, branching, math, heuristics,
  parameter choices, ordering decisions.
- 0.5: a moderate body with some logic but mostly straight-line code.
- 0.0: a few-line wrapper or pass-through with nothing to mutate.

### 5. Eval cost  (weight 0.15)
- 1.0: single eval is fast (<5s) — cheap to run for hundreds of generations.
- 0.5: single eval is moderate (5s–60s).
- 0.0: single eval is slow (>60s) — only worth evolving with a strong prior.

### 6. Domain pattern match  (weight 0.10)
- 1.0: matches a known archetype (see `domain-patterns.md`).
- 0.5: partial match.
- 0.0: no recognizable archetype.

## Combined Score

`combined = 0.20*A1 + 0.15*A2 + 0.20*A3 + 0.20*A4 + 0.15*A5 + 0.10*A6`

## Cut-off

Recommend `combined >= 0.6` as the cut-off for inclusion in `report.md`'s
top section. Lower-scoring functions go into `report.json` only, marked as
`tier: low`.

## Rationale Field

For every candidate, the report must include a one-paragraph rationale
explaining the dominant axis scores and what an evolved version would look
like.
