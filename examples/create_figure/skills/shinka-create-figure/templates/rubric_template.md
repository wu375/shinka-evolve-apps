# Figure Judge Rubric (TEMPLATE — fill in every section)

> The judge sees only the rendered PNG and this rubric. It does not see the
> Python source code, mutation history, or evaluator internals.

## Research context
<!-- One paragraph describing the paper / project / experiment. What is being
studied? What method or system does the figure relate to? -->

## Figure intent
<!-- What should the figure communicate? E.g. "the architecture of method X",
"the qualitative trade-off between latency and accuracy", "the conceptual
sequence of steps in the algorithm". -->

## Required visual elements
<!-- A bullet list of concepts, modules, axes, labels, relationships, etc. that
must appear or be readable. -->

## Target audience
<!-- E.g. "expert reader of an ML paper", "broad scientific audience",
"keynote slide", "tutorial figure for newcomers". -->

## Allowed assumptions / data policy
<!-- State whether the figure uses dummy data, a fixed loader, conceptual
geometry only, etc. The judge MUST treat the figure as a visual / conceptual
prototype unless the context explicitly grants data faithfulness. -->

## Visual priorities
<!-- Rank or describe priorities: clarity, density, publication-readiness,
hierarchy, elegance, color discipline, whitespace, etc. -->

## Failure modes the judge must flag
- rendering bugs (clipping, overlap, broken arrows)
- unreadable text (too small, off-canvas, low contrast)
- clutter or visual noise that hurts the message
- missing required concepts
- misleading visual implications (e.g. fake precision in placeholder data)
- apparent mismatch with the research context

## Scoring fields (return EXACTLY these in JSON)
| Field                 | Type    | Range  | Meaning                                         |
| --------------------- | ------- | ------ | ----------------------------------------------- |
| `overall_score`       | number  | 0–10   | Holistic quality. Used as Shinka `combined_score`. |
| `visual_plausibility` | number  | 0–10   | Looks like a believable research figure.        |
| `context_alignment`   | number  | 0–10   | Matches the research context above.             |
| `clarity`             | number  | 0–10   | Readable text, hierarchy, no clutter.           |
| `rendering_quality`   | number  | 0–10   | No rendering bugs.                              |
| `rationale`           | string  |        | 2–4 sentences explaining the scores.            |
| `issues`              | array   |        | Short bullet strings of observed problems.      |

## Judge instructions
- Judge **only** the rendered image.
- Do not assume the underlying data is real unless this rubric says so.
- Penalize rendering bugs and unreadable text aggressively.
- Reward visual hierarchy, restraint, and alignment with the research context.
- If a required visual element is missing, lower `context_alignment` and list
  it under `issues`.

## Defaults used
<!-- Filled in by the skill at scaffold time. List every input that was not
explicitly supplied by the user and the default that was applied (e.g.
"target audience: expert scientific reader (default)", "figure type:
matplotlib (default)", "judge model: gemini-3-flash-preview (default)"). -->

