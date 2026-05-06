# Self-Play Novelty Rubric (TEMPLATE)

> The judge receives multiple 2x2 gallery image inputs in shuffled order. The
> prompt maps those inputs to Image 1, Image 2, ... The judge does not know
> which gallery is the candidate.

## Creative Brief

<!-- Describe the open-ended visual domain or theme. Do not describe a single
target image. -->

## Evaluation Goal

Reward galleries that would strengthen the opponent pool: visually compelling,
meaningfully different from existing pool members, and likely to push future
candidates toward richer generative mechanisms across stochastic samples.

## Criteria

The judge must rank all gallery images independently for each criterion:

- `divergence` — meaningful visual difference from the other images; not just
  minor palette or layout variation.
- `aesthetic_coherence` — intentional, legible, color-aware, compositionally
  engaging.
- `mechanism_breadth` — evidence of rich generative structure: fields, tilings,
  growth systems, path grammars, layering, masks, symbolic diagrams, symmetry
  breaking, typography, or other distinct strategies.
- `surprise` — open-ended discovery value; makes future unseen variants feel
  promising rather than predictable.
- `theme_alignment` — engages the creative brief through imagery, mood,
  material qualities, or concept without collapsing to a single literal motif.

## Ranking Instructions

- Rank `1` is best.
- Use every rank from `1` to `N` exactly once for each criterion, where `N` is
  the number of images provided.
- The rank arrays must be in input image order: Image 1, Image 2, ...
- Do not tie ranks.
- Judge only the visible galleries, not source code or assumed generation history.
- Penalize broken rendering, unreadable clutter, low-effort templates, and
  random noise without visual intent.

## Required JSON Shape

Return only JSON:

```json
{
  "ranks": {
    "divergence": [1, 3, 2, 4],
    "aesthetic_coherence": [2, 1, 4, 3],
    "mechanism_breadth": [1, 4, 2, 3],
    "surprise": [3, 1, 2, 4],
    "theme_alignment": [2, 3, 1, 4]
  }
}
```

## Defaults Used

<!-- Fill in at scaffold time. Include n_opponents, n_games, base_seed,
judge model, proposal models, pool policy, and any default criteria. -->
