---
name: shinka-self-play-novelty
description: Create ShinkaEvolve tasks for open-ended SVG/image novelty where candidates are judged against a fixed opponent image pool. Use when evolving generative art, visual novelty, SVG generators, or self-play-style multimodal ranking loops where stronger images are promoted between Shinka runs.
---

# Shinka Self-Play Novelty Skill

Use this skill to set up a ShinkaEvolve visual novelty task with a
self-play-style evaluator. Instead of scoring a candidate image in isolation,
each evaluation samples opponent images from a fixed pool, shuffles the
candidate among them, and asks a Gemini multimodal judge to rank every image
for each rubric criterion.

The judge never knows which image is the current candidate.

Use `uv` for package management unless the user explicitly asks for another
package manager.

## Composition With Existing Shinka Skills

The core ShinkaEvolve skills live in the `skills/` directory of the
ShinkaEvolve repo. If they are not loaded, first check whether a source clone
exists in the current project or directory tree. If not found, install via
`uv pip install shinka-evolve`.

- Use `shinka-setup` for generic single-run task setup.
- Use this skill when the evaluator should compare images against a fixed
  opponent pool.
- Use `shinka-run` for each individual ShinkaEvolve batch.
- Use `shinka-inspect` or the included round script between batches to promote
  top images into the next opponent pool.

## When To Use

Trigger phrases:

- "Evolve generative art with self-play novelty."
- "Rank each candidate against an image pool."
- "Use ShinkaEvolve for SVG art with stronger opponents over rounds."
- "Update the visual opponent pool between runs."

Do not use this for numeric metrics, game agents, or research figures with a
fixed target image. Use `shinka-setup`, `shinka-self-play`, or
`shinka-create-figure` instead.

## Required Inputs

1. **Creative brief / theme** — open-ended guidance for the visual domain.
2. **Rubric criteria** — named criteria used by the judge. Defaults:
   divergence, aesthetic coherence, mechanism breadth, surprise, theme alignment.
3. **Baseline opponent pool** — at least `n_opponents` images in `opponents/`.
   PNG is preferred; SVG is accepted by the evaluator template.
4. **Self-play schedule** — rounds, generations per round, promotion count, and
   pool window size.
5. **A `.env` file** with `GEMINI_API_KEY` for the multimodal judge and any
   proposal-LLM keys used by Shinka.

Defaults:

- `n_opponents = 3`
- `n_games = 2`
- `base_seed = 42`
- judge model: `gemini-3-flash-preview`
- proposal model: `gemini-3-flash-preview`

## Candidate Contract

Every candidate is a single Python file exposing:

```python
def generate_svg(rng: int) -> str:
    """Return a complete, self-contained SVG artwork."""
```

The evaluator calls `generate_svg(seed)`, validates the SVG, renders it to PNG,
and uses that PNG as the hidden target image in each ranking game.

SVG constraints:

- valid XML with an `<svg>` root,
- includes `viewBox` or width/height,
- no scripts, event handlers, external links, `data:` URLs, or `foreignObject`,
- bounded output size and element count.

## Evaluator Contract

Generate `evaluate.py` from `templates/evaluator_template.py`. It must:

1. load `.env` from the task workspace or parent directories,
2. import the candidate from `program_path`,
3. call `generate_svg(seed)` and render the target PNG,
4. load a fixed opponent pool from `opponents/` or a frozen
   `pool_snapshot/`,
5. for each of `n_games`, deterministically sample `n_opponents` opponents,
   shuffle target + opponents, and preserve that shuffled image order,
6. send each PNG to Gemini as a separate image part followed by a prompt that
   maps those parts to `Image 1`, `Image 2`, ... in order,
7. parse JSON ranks shaped like:

```json
{
  "ranks": {
    "divergence": [2, 1, 4, 3],
    "aesthetic_coherence": [1, 3, 2, 4]
  }
}
```

The rank arrays are in input image order. Rank `1` is best. The judge must use
all ranks `1..n_opponents + 1` exactly once per criterion.

## Score Calculation

For each rubric criterion in each game:

```text
normalized = (num_images - target_rank) / (num_images - 1)
```

So best rank scores `1.0`; worst rank scores `0.0`. The final
`combined_score` is the mean normalized score across all criteria and games.

Return Shinka-compatible metrics:

```json
{
  "combined_score": 0.0,
  "public": {
    "divergence": 0.0,
    "aesthetic_coherence": 0.0,
    "avg_target_rank": 0.0,
    "first_place_rate": 0.0
  },
  "private": {
    "games": [],
    "pool_size": 0
  },
  "text_feedback": "concise score summary and improvement directions"
}
```

The proposal LLM should see `public` scores and `text_feedback`, but not the
hidden target index or opponent identities.

## Opponent Pool Policy

The pool is fixed within each ShinkaEvolve run and updated only between runs.

Workspace layout:

```text
<task_workspace>/
  initial.py
  evaluate.py
  rubric.md
  shinka.yaml
  opponents/
    baseline_001.png
    baseline_002.png
  rounds/
    round_001/
      pool_snapshot/
      results/
      promoted/
```

For round `N`:

1. copy `opponents/` to `rounds/round_N/pool_snapshot/`,
2. configure the evaluator to read that snapshot,
3. run one ShinkaEvolve batch,
4. collect top candidate images from the round results,
5. promote top-K images into `opponents/`,
6. apply a fixed-size sliding window to old non-baseline opponents.

Baseline images should be marked with a `baseline_` prefix and kept forever.
Promoted images should include round and rank in their filename.

## Tuning Guidance

The evaluator is the fitness landscape. Tune it before running a full batch.

- If scores are too flat, increase rubric specificity or add stronger/diverse
  baselines.
- If judging is too slow, reduce `n_games` before reducing `n_opponents`.
- If candidates overfit to one style, rotate in behaviorally different promoted
  images, not only visually similar winners.
- Keep sampling seeded. Identical evaluator inputs should produce the same
  opponent samples and image order.

During smoke tests and runs, monitor logs. ShinkaEvolve may retry indefinitely
on transient LLM API failures, quota issues, or bad model names.

## Workflow

1. Create a dedicated task workspace, e.g.
   `shinka_tasks/<theme_name>_self_play_novelty/`.
2. Copy templates:
   - `templates/seed_svg_template.py` → `initial.py`
   - `templates/evaluator_template.py` → `evaluate.py`
   - `templates/rubric_template.md` → `rubric.md`
   - `templates/shinka_config_template.yaml` → `shinka.yaml`
   - `templates/run_self_play_novelty.py` → `run_self_play_novelty.py`
3. Fill `rubric.md` with the creative brief, criteria, and defaults used.
4. Put baseline PNG/SVG images in `opponents/`.
5. Smoke-test the evaluator:

```bash
python evaluate.py --program_path initial.py --results_dir /tmp/self_play_novelty_smoke
```

Confirm `metrics.json` contains `combined_score`, per-criterion public scores,
private game records, and usable `text_feedback`.

6. Run a 2-generation smoke batch before the full loop:

```bash
shinka run --config shinka.yaml --num-generations 2
```

7. Run the multi-round loop and promote top images between rounds:

```bash
python run_self_play_novelty.py --rounds 3 --generations-per-round 20
```

8. After the run, report the top images, score trajectory, promoted pool
changes, and any judge caveats.

## Files

- `SKILL.md` — this file.
- `templates/seed_svg_template.py` — starter candidate with
  `generate_svg(rng)`.
- `templates/evaluator_template.py` — ranking-based Gemini evaluator.
- `templates/rubric_template.md` — rubric and judge instructions template.
- `templates/shinka_config_template.yaml` — low-budget local config template.
- `templates/run_self_play_novelty.py` — fixed-pool multi-round helper.
