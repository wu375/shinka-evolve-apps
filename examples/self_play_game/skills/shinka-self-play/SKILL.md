---
name: shinka-self-play
description: Evolve a heuristic game agent using ShinkaEvolve with a self-play-inspired evaluator. Runs ShinkaEvolve against a fixed pool of past-version opponents, then periodically rotates the pool with newer top agents and reruns. Game-agnostic; the user supplies a game environment, an agent interface, and a baseline opponent pool. Use this skill whenever the user wants to evolve a bot, run self-play loops, or improve a game agent through evolutionary search.
---

# Shinka Self-Play Skill

`shinka-self-play` orchestrates a multi-run ShinkaEvolve workflow that
treats the agent's own history as the opponent pool. After each Shinka
run, the top agents become opponents for the next round, so the meta the
agent must beat steadily strengthens.

This skill is **game-agnostic**. The user plugs in their own game
environment via documented contracts. Game-specific implementations live
in named subdirectories (see Files section).

## Composition With Existing Shinka Skills

The core ShinkaEvolve skills live in the `skills/` directory of the
ShinkaEvolve repo. If the skills are not already loaded by the agent,
first check whether a source clone of ShinkaEvolve exists in the current
project or directory tree. If not found, install via
`uv pip install shinka-evolve` (prefer `uv` for package management unless
the user or project explicitly uses something else).

- Use `shinka-setup` for one-shot tasks; this skill *wraps* multiple
  Shinka runs into a self-play loop.
- Reuses `shinka-run` for each individual evolution batch.
- Reuses `shinka-inspect` between rounds to pick which top agents become
  next-round opponents.
- Composes with `shinka-scan-repo`: if you found a candidate via the
  scanner with `domain_pattern == game-agent-policy`, hand it here.

## When To Use

Trigger phrases:

- "Use Shinka to evolve an agent for <game>."
- "Run self-play with ShinkaEvolve."
- "Improve this game agent via evolution."
- "Set up a self-play loop where the opponent pool grows over rounds."

The user will either name a specific game (e.g. "evolve an agent for
Orbit Wars") or provide their own game definitions. For known games, look
for a game-specific subdirectory under this skill that contains ready-made
game runners, seed agents, and baseline bots.

## Required User Inputs

1. **Game environment** — a module exposing
   `run_match(agent_a, agent_b, seed) -> dict` that simulates one match
   and returns `{"candidate_score": float, "placement": int}` (1 = win).
2. **Agent interface** — the candidate file must expose
   `act(observation: dict, config: dict | None = None) -> action`.
3. **Baseline opponent pool** — at least one or two hand-authored bots to
   seed `bots/` before the first round.
4. **Self-play schedule** — number of rounds, generations per round, pool
   rotation policy (top-K agents kept, oldest dropped, or windowed).
5. **A `.env` file** with API keys for the LLM providers used by Shinka
   (e.g. `GEMINI_API_KEY`, `OPENAI_API_KEY`). These are consumed by the
   proposal LLMs during evolution, not by the evaluator — the evaluator
   scores numerically via match results.

## LLM Model Selection

Default to `gemini-3-flash-preview` only for proposal LLMs in
`shinka.yaml`. Confirm with the user before adding other models (e.g.
OpenAI, Anthropic) — each requires its own API key and adds cost.

## Shinka Config Defaults

- **`parallel_evaluators: 2`** — self-play evaluators tend to be heavy
  (multiple matches per evaluation). Default to 2 parallel evaluators to
  avoid overwhelming the machine. Increase only after confirming that
  CPU / memory headroom exists.
- **`use_text_feedback: true`** — forward the evaluator's `text_feedback`
  string to proposal LLMs as natural-language guidance on strengths and
  failure modes. Essential for game agents where per-criterion
  breakdowns alone are not enough to guide mutations.

## Workspace Layout

For each ShinkaEvolve run, create a dedicated scaffold directory under
`shinka_tasks/`, e.g. `shinka_tasks/<game_name>_self_play/scaffold/`.
Use this scaffold directory as `<task_workspace>` and run Shinka commands
from it unless the installed ShinkaEvolve version requires an explicit
workspace flag.

```
<task_workspace>/
  initial.py                # candidate agent (act-style interface)
  evaluate.py               # game-aware evaluator using bots/ as pool
  game_runner.py            # user-pluggable match runner
  bots/                     # opponent pool for the CURRENT round
    <baseline>.py           # user-supplied baselines
    gen<round>_top<k>.py    # added between rounds by the orchestrator
  shinka.yaml               # config for the current round
  run_self_play.py          # multi-round orchestration
  rounds/
    round_<N>/
      results/...           # ShinkaEvolve outputs for round N
      pool_snapshot/        # frozen copy of bots/ used this round
      top_k.json            # agents promoted from this round
```

## Self-Play Loop

For round `N` in 1..R:

1. Snapshot `bots/` into `rounds/round_<N>/pool_snapshot/`.
2. If round 1: `initial.py` is the user's seed.
   Otherwise: `initial.py` is the previous round's top agent.
3. Run a single ShinkaEvolve batch via `shinka-run`.
4. Use `shinka-inspect` to pull the top-K agents from the round's DB.
5. Promote them into `bots/` (rotation policy below).
6. Continue to round N+1.

## Pool Rotation Policy

Default policy: **fixed-size sliding window**.

- `pool_size = baseline + K * rounds_kept`.
- After each round, append the round's top-K agents.
- If `len(bots) > pool_size`, drop the oldest non-baseline agents first.
- Hand-authored baseline bots are never dropped; they anchor the pool
  against catastrophic forgetting.

Configurable parameters:

- `top_k` — agents promoted per round (default 2).
- `rounds_kept` — how many recent rounds' promotions to keep (default 3).
- `keep_baselines` — true / false (default true).
- `windowing` — `sliding` (default) or `cumulative` (never drop).

## Evaluator Design

The evaluator is the most critical component of the self-play loop. It
must be **carefully designed and tuned** before starting evolution — a
poorly calibrated evaluator produces meaningless fitness signals.

### Key Design Decisions

These parameters interact and must be balanced by trial and error:

- **Opponent pool size** — how many bots in `bots/` to play against per
  evaluation. More opponents = better signal, but linearly increases
  eval time. Start small (2–3 baselines) and grow via pool rotation.
- **Opponent diversity** — the pool should cover different play styles
  (aggressive, defensive, economic, random). A pool of near-identical
  bots gives redundant signal. When promoting top-K agents between
  rounds, prefer behaviorally diverse agents over the K highest scorers.
- **Matches per opponent** — how many repeated games (with different
  seeds) to play against each opponent. More repeats reduce variance but
  increase cost. 3–5 seeds per opponent is a reasonable starting point.
- **Random seeds** — use deterministic seeds for reproducibility. Vary
  seeds across matches but keep them fixed within a single evaluation so
  results are comparable across candidates.
- **Eval time vs. eval quality** — total eval time =
  `num_opponents × matches_per_opponent × time_per_match`. With
  `parallel_evaluators: 2`, each evaluation blocks a slot. Budget the
  per-candidate eval time so a full generation completes in reasonable
  wall-clock time (minutes, not hours).
- **Pool update frequency** — how often to rotate new agents into the
  pool (default: every round). Updating too frequently destabilizes the
  fitness landscape; too infrequently lets candidates overfit to a stale
  meta.

### Tuning Workflow

1. Smoke-test the evaluator against the seed agent and baselines.
2. Review `text_feedback` and per-opponent breakdowns. A good evaluator
   should produce differentiated scores — not blanket wins or losses.
3. If eval is too slow, reduce matches per opponent or pool size.
4. If scores are noisy (high variance across identical runs), increase
   matches per opponent or add more seeds.
5. If all candidates score similarly, the pool may lack diversity — add
   opponents with different strategies.
6. Repeat until the evaluator reliably distinguishes strong from weak
   agents in reasonable time.

**Important:** ShinkaEvolve may not exit immediately even when a fatal
error has occurred. During smoke tests and runs, actively monitor log
output and interrupt (`Ctrl-C`) if fatal retry output repeats without
new progress. Fix the underlying issue before re-running.

### Evaluator Contract

The evaluator must:

1. Load the candidate from `program_path`.
2. Load every bot in `bots/` as an opponent.
3. Call `game_runner.run_match(candidate, opponent, seed)` for each
   (opponent × seed) pair.
4. Score using a robustness-oriented blend — don't reward exploiting a
   single weak baseline. Suggested components: win rate, score margin,
   placement, trajectory share vs. strongest opponent.
5. Return Shinka-compatible metrics:

```json
{
  "combined_score": "<float, higher better>",
  "public": {"win_rate": "...", "avg_score": "..."},
  "private": {},
  "text_feedback": "<short summary of strengths and failure modes>"
}
```

## Game Runner Contract

`game_runner.py` is the only game-specific file. It must expose:

```python
def run_match(agent_a, agent_b, seed: int) -> dict:
    """Run one match. Return:
        {"candidate_score": float,  # higher = better for agent_a
         "placement": int}          # 1 if agent_a wins/ties, 2 otherwise
    """
```

## Determinism

- Each match uses a deterministic seed. The same evaluator inputs
  reproduce the same outcomes.
- The pool snapshot for round N is frozen at round start; agents
  promoted mid-round do not affect that round's evaluations.

## Forbidden Patterns

The candidate must not:

- read or write outside the evaluator-provided directories,
- spawn subprocesses against external systems,
- access the network,
- import modules from `bots/` (opponents are loaded by the evaluator,
  not by the candidate).

## Workflow

1. Collect inputs (game, interface, baselines, schedule).
2. Create a **dedicated run scaffold directory** under `shinka_tasks/`
   (e.g. `shinka_tasks/<game_name>_self_play/scaffold/`) following the
   workspace layout above. For known games, copy files from the matching
   game subdirectory (e.g. `kaggle-orbit-wars/`). For custom games, the
   user provides the game runner, seed agent, and baseline bots.
3. Smoke-test the evaluator against the seed agent. Tune evaluator
   parameters (pool size, matches per opponent, seeds) by trial and
   error until scoring is reliable and eval time is acceptable.
   Monitor logs frequently and interrupt if ShinkaEvolve repeats fatal
   retry output or stops making progress.
4. **2-generation smoke run** before the full self-play loop. Run
   Shinka for exactly 2 generations on round 1 to confirm the end-to-end
   pipeline works (proposal LLM → evaluator → match results → metrics
   → next proposal):

   ```bash
   shinka run --config shinka.yaml --num-generations 2
   ```

   Confirm both generations produce valid candidates with differentiated
   `combined_score` values and readable `text_feedback`. If any
   generation fails or all candidates score identically, fix the issue
   before proceeding with the full run.

   **Monitoring requirement:** ShinkaEvolve may not exit immediately
   even when a fatal error has occurred. Watch progress frequently
   during smoke and full runs: check logs every 30–60 seconds during
   startup and smoke runs, then at least every few minutes during
   longer batches. Kill the run quickly (`Ctrl-C` or terminate the
   process) if you see repeated fatal errors such as authentication
   failures, quota exhaustion, invalid model names, import errors, or
   identical retry messages with no new proposal/evaluation/generation
   progress. Fix the root cause before re-running.

5. Run the multi-round self-play loop, delegating each round to
   `shinka-run` and using `shinka-inspect` + the rotation policy between
   rounds. Apply the same frequent-monitoring rule to every round and
   kill quickly if progress stalls on fatal retry output.
6. Final report: top agent across rounds, win-rate summary vs. pool.

## Files

- `SKILL.md` — this file.
- `kaggle-orbit-wars/` — complete game-specific package for the Kaggle
  Orbit War strategy game, including game runner, seed agent, baseline
  bots, evaluator, and game rules documentation.
