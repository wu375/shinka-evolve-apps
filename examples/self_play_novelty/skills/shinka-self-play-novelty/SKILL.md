---
name: shinka-self-play-novelty
description: Create ShinkaEvolve tasks for open-ended SVG/image novelty where candidates are judged against a fixed opponent image pool. Use when evolving generative art, visual novelty, SVG generators, or self-play-style multimodal ranking loops where stronger images are promoted between Shinka runs.
---

# Shinka Self-Play Novelty Skill

Use this skill to set up a ShinkaEvolve visual novelty task with a
self-play-style evaluator. Instead of scoring a candidate image in isolation,
each evaluation renders four stochastic samples from the candidate program,
composes them into a single 2x2 PNG gallery, samples opponent galleries from a
fixed pool, shuffles the candidate gallery among them, and asks a Gemini
multimodal judge to rank every gallery for each rubric criterion.

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
3. **Baseline opponent pool** — at least `n_opponents` gallery images in
   `opponents/`. PNG galleries are preferred; each opponent should represent
   four samples in a 2x2 grid so candidate and opponent units match.
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
# EVOLVE-BLOCK-START
def generate_svg(rng: int) -> str:
    """Return a complete, self-contained SVG artwork."""
# EVOLVE-BLOCK-END
```

**The `EVOLVE-BLOCK-START / EVOLVE-BLOCK-END` markers in `initial.py` are
mandatory.** `apply_full_patch` calls `_mutable_ranges()` on the original file
to locate the region to replace. If no markers are present it returns
`"No EVOLVE-BLOCK regions found in original content"` and every proposal fails.
The seed_svg_template.py already includes these markers — do not remove them.

The evaluator calls `generate_svg(seed)` four times with deterministic seeds.
The function should be stochastic with respect to the seed so the gallery shows
meaningful variation across samples.

SVG constraints:

- valid XML with an `<svg>` root,
- includes `viewBox` or width/height,
- no scripts, event handlers, external links, `data:` URLs, or `foreignObject`,
- all imports inside the function body (Shinka runs the function in isolation).

## Evaluator Contract

Generate `evaluate.py` from `templates/evaluator_template.py`. It must:

1. load `.env` from the task workspace or parent directories,
2. import the candidate from `program_path`,
3. call `generate_svg(seed)` four times, render the sample PNGs, and compose
   `gallery.png` as a 2x2 candidate gallery,
4. write the rendered gallery under the evaluation `results_dir` so Shinka runs
   keep it in the per-generation folder such as `results/gen_x/`,
5. load a fixed opponent gallery pool from `opponents/` or a frozen
   `pool_snapshot/`,
6. for each of `n_games`, deterministically sample `n_opponents` opponents,
   shuffle target gallery + opponent galleries, and preserve that shuffled image order,
7. send each gallery PNG to Gemini as a separate image part followed by a prompt that
   maps those parts to `Image 1`, `Image 2`, ... in order,
8. parse JSON ranks shaped like:

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
    "pool_size": 0,
    "target_gallery": "results/gen_x/gallery.png"
  },
  "extra_data": {"image_path": "results/gen_x/gallery.png"},
  "text_feedback": "concise score summary and improvement directions"
}
```

The proposal LLM should see `public` scores and `text_feedback`, but not the
hidden target index or opponent identities. Use `extra_data.image_path` for the
candidate gallery PNG, not an individual sample.

## Opponent Pool Policy

The pool is fixed within each ShinkaEvolve run and updated only between runs.

Workspace layout:

```text
shinka_tasks/<theme_name>_self_play_novelty/
  initial.py         ← must have EVOLVE-BLOCK-START/END markers
  evaluate.py
  rubric.md
  shinka.yaml
  run_self_play_novelty.py
  opponents/
    baseline_001.png  # 2x2 gallery
    baseline_002.png  # 2x2 gallery
  galleries/          # auto-created; one gallery PNG per evaluated generation
  rounds/
    round_001/
      pool_snapshot/
      results/
        gen_0/
          results/
            gallery.png
            metrics.json
      promoted.json
```

For round `N`:

1. copy `opponents/` to `rounds/round_N/pool_snapshot/`,
2. delete `evolution_db.sqlite` so each round starts with a clean ancestry,
3. run one ShinkaEvolve batch writing results to `rounds/round_N/results/`,
4. collect top candidate gallery PNGs — **deduplicated by image path** (Shinka
   may write metrics.json to both `gen_N/results/` and `best/results/`, both
   pointing at the same gallery; without dedup two promotion slots go to the
   same image),
5. promote top-K gallery PNGs into `opponents/`,
6. apply a fixed-size sliding window to old non-baseline opponents.

Baseline gallery images should be marked with a `baseline_` prefix and kept
forever. Promoted galleries should include round and rank in their filename.

## Known Pitfalls

### SVG renderer: use rsvg-convert, not cairosvg

**cairosvg silently renders `hsl()` CSS colors as black** — the image is
rendered but all hsl-colored elements appear as solid black, making evaluation
meaningless. Use `rsvg-convert` as the primary renderer. The evaluator template
puts rsvg-convert first and falls back to ImageMagick, then cairosvg as a last
resort. Install rsvg-convert via `brew install librsvg` (macOS) or
`apt install librsvg2-bin` (Linux).

### rubric.md path

Shinka copies `evaluate.py` into a temporary results directory. `__file__`
inside evaluate.py then points to that temp dir, not the task workspace, so
`Path(__file__).parent / "rubric.md"` will fail. The evaluator template
searches for `rubric.md` by walking upward from `pool_dir.parent` (the task
workspace, derivable from the absolute `opponents_dir` argument) and from
`results_dir` parents. This finds rubric.md regardless of where the evaluator
copy lives.

### EVOLVE-BLOCK markers in initial.py

The full-patch applier looks for `EVOLVE-BLOCK-START / EVOLVE-BLOCK-END`
markers in the original file to locate the mutable region. Without them every
proposal fails with `"No EVOLVE-BLOCK regions found in original content"`.
Wrap the entire `generate_svg` function in these markers in `initial.py`.

### LLM generating EVOLVE markers in proposals

Despite the markers being required in `initial.py`, the proposal LLM sometimes
generates `# EVOLVE-BLOCK-START/END` comments **inside** the generated function
body. The regex `(?:#|//|)?\s*EVOLVE-BLOCK-*` matches Python comment style, so
inner markers cause `patch_has_both=True`, extracting only the inner section
and discarding the rest of the generated function. Prevent this with an explicit
system-prompt rule:

```
FORMATTING RULE: Output ONLY the complete Python function. Do NOT include
EVOLVE-BLOCK-START, EVOLVE-BLOCK-END, or any other special block markers.
Do NOT include markdown fences or any text outside the function definition.
```

### shinka_run CLI flags

The CLI is `shinka_run` (not `shinka run`). Flag names differ from older docs:

```bash
shinka_run \
  --task-dir <workspace> \
  --results_dir <results_dir> \
  --num_generations <N> \
  --config-fname shinka.yaml
```

Note: `--results_dir` uses an underscore; `--task-dir` uses a hyphen.

### shinka.yaml: opponents_dir must be an absolute path

Shinka copies `evaluate.py` to a temp directory and runs it from there.
Relative `opponents_dir` paths resolve against that temp dir, not the task
workspace, and will fail with "opponent pool not found". Always use an absolute
path in `job_config.extra_cmd_args.opponents_dir`.

### shinka.yaml: do not put thinking_budget in llm_kwargs

Adding `thinking_budget: 0` to the YAML `llm_kwargs` block causes:
`AsyncLLMClient.__init__() got an unexpected keyword argument 'thinking_budget'`.
ShinkaEvolve automatically sets `thinking_budget=0` as a query-time kwarg for
models classified as reasoning models when `reasoning_efforts="disabled"`.
Do not configure it in the YAML.

### SVG size limit

The default `max_svg_characters` limit of 50,000 is too small for legitimate
complex artwork. Programs that approximate smooth curves as polylines (many `L`
lineto commands) or render large grids of elements can easily produce 500K–1M
character SVGs. Since SVG rendering is a local deterministic operation (no API
cost or network overhead), raise the limit to **2,000,000** characters. The
`text_feedback` from a failed evaluation will include the actual character count
so the LLM can self-correct.

### Judge timeout for Vertex AI

On Vertex AI, TCP connections can enter a zombie state and the judge call hangs
indefinitely. Wrap the judge call in a `ThreadPoolExecutor` with a 90-second
timeout:

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    return executor.submit(_call).result(timeout=90)
```

### shinka.yaml config format

Use `job_config.extra_cmd_args` to pass evaluator arguments, not the old
`evaluate_function:` block format. The working format:

```yaml
job_config:
  python_executable: /absolute/path/to/.venv/bin/python
  extra_cmd_args:
    opponents_dir: /absolute/path/to/opponents
    n_opponents: 3
    n_games: 2
    base_seed: 42
    judge_model: gemini-3-flash-preview
```

### Debugging Gemini None responses

When Shinka retries indefinitely, the cause is often `candidates=None` from
Gemini (safety filter, quota, or transient error). To diagnose, add logging
to ShinkaEvolve's `shinka/llm/providers/gemini.py` in
`gemini_extract_thoughts_and_content`:

```python
def _log_raw_response(response):
    logger.info(
        f"RAW GEMINI RESPONSE | prompt_feedback={getattr(response,'prompt_feedback',None)!r} | "
        f"candidates={getattr(response,'candidates',None)!r}"
    )
```

Then inspect `finish_reason` and `safety_ratings` on each candidate.
Since this is an editable install the change takes effect after restarting
the shinka_run process.

## Tuning Guidance

The evaluator is the fitness landscape. Tune it before running a full batch.

- If scores are too flat, increase rubric specificity or add stronger/diverse
  baselines.
- If judging is too slow, reduce `n_games` before reducing `n_opponents`; keep
  the four-sample gallery so stochastic programs are judged consistently.
- If candidates overfit to one style, rotate in behaviorally different promoted
  images, not only visually similar winners.
- Keep sampling seeded. Identical evaluator inputs should produce the same
  opponent samples and image order.

During smoke tests and runs, monitor logs. ShinkaEvolve may retry indefinitely
or fail to exit quickly even when a fatal error has occurred, including on
transient LLM API failures, quota issues, bad model names, auth failures, or
import errors. Check progress every 30–60 seconds during startup and smoke
runs, then at least every few minutes during longer batches. Kill the run
quickly (`Ctrl-C` or terminate the process) if fatal errors repeat, retry
messages are identical, or no new proposal/evaluation/generation progress is
visible. Fix the root cause before re-running.

## Workflow

1. Create a task workspace directory:
   ```
   shinka_tasks/<theme_name>_self_play_novelty/
   ```

2. Copy templates:
   - `templates/seed_svg_template.py` → `initial.py`
   - `templates/evaluator_template.py` → `evaluate.py`
   - `templates/rubric_template.md` → `rubric.md`
   - `templates/shinka_config_template.yaml` → `shinka.yaml`
   - `templates/run_self_play_novelty.py` → `run_self_play_novelty.py`

3. Customize `shinka.yaml`:
   - Set `task_sys_msg` theme description.
   - Set `job_config.python_executable` to the venv python.
   - Set `job_config.extra_cmd_args.opponents_dir` to the **absolute** path of
     `opponents/`.

4. Fill `rubric.md` with the creative brief and judging criteria.

5. Generate baseline 2x2 gallery PNGs using `rsvg-convert` (not cairosvg) and
   put them in `opponents/` with `baseline_` prefix. Use at least `n_opponents`
   images. Generate them with the same 2×2 layout (four seeds, one grid PNG)
   so they match the candidate gallery format.

6. Smoke-test the evaluator:
   ```bash
   python evaluate.py --program_path initial.py \
       --results_dir /tmp/self_play_novelty_smoke \
       --opponents_dir /absolute/path/to/opponents
   ```
   Confirm `metrics.json` contains `combined_score`, per-criterion public
   scores, private game records, `extra_data.image_path` pointing at
   `gallery.png`, and usable `text_feedback`. Visually verify the gallery PNG
   is not all-black (would indicate cairosvg HSL rendering bug).

7. Run a 2-generation smoke batch before the full loop:
   ```bash
   shinka_run --task-dir . --results_dir /tmp/smoke_results \
              --num_generations 2 --config-fname shinka.yaml
   ```
   Monitor this smoke run frequently and kill it quickly if it repeats fatal
   errors or stops making proposal/evaluation/generation progress.

8. Run the multi-round loop:
   ```bash
   python run_self_play_novelty.py --workspace . --rounds 3 \
       --generations-per-round 20 --top-k 2
   ```
   Apply the same frequent-monitoring rule to the round helper.

9. After the run, report the top images, score trajectory, promoted pool
   changes, and any judge caveats. Rendered galleries are in `galleries/`
   (one per generation) and `review/round_N/` (sorted by score).

## Files

- `SKILL.md` — this file.
- `templates/seed_svg_template.py` — starter candidate with
  `generate_svg(rng)` wrapped in EVOLVE-BLOCK markers.
- `templates/evaluator_template.py` — ranking-based Gemini evaluator with
  rsvg-convert priority, judge timeout, rubric path search, and gallery copy.
- `templates/rubric_template.md` — rubric and judge instructions template.
- `templates/shinka_config_template.yaml` — config using `job_config` format.
- `templates/run_self_play_novelty.py` — fixed-pool multi-round helper with
  correct `shinka_run` CLI, DB reset per round, and deduplication.
