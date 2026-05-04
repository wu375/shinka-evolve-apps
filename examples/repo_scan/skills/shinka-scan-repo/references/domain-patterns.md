# Domain Patterns

When a candidate function matches one of these archetypes, the scanner can
propose specific evaluation metrics and bump the pattern-match axis.

A "match" requires both a structural signal (function shape / call sites)
and a naming signal (function/variable names hinting at the role).

## Sampler
**Signals**: returns a sequence / generator of items, takes a budget or
count, uses RNG, or balances exploration vs. exploitation.
**Naming**: `sample`, `propose`, `select`, `draw`, `acquire`, `pick`.
**Suggested metrics**: coverage of a target distribution, expected reward,
mean log-probability of accepted items, time-to-first-success.

## Optimizer step / update rule
**Signals**: takes parameters + gradients (or losses) and returns updated
parameters; loop body of an iterative method.
**Naming**: `step`, `update`, `apply`, `optimize`, `iterate`.
**Suggested metrics**: final loss after fixed iterations, iterations to
reach target, stability across seeds.

## Numerical kernel
**Signals**: tight numeric inner loop, often `@njit` / `@torch.compile` /
NumPy vectorization, returns an array.
**Naming**: `kernel`, `compute_*`, `solve_*`, `propagate`, `step_*`.
**Suggested metrics**: runtime, throughput, accuracy vs. reference.

## Scheduler
**Signals**: takes a current step / epoch / state and returns a value
controlling another process (learning rate, temperature, batch size,
exploration epsilon, etc.).
**Naming**: `schedule`, `get_lr`, `temperature`, `epsilon`, `cool`.
**Suggested metrics**: downstream task performance after fixed budget,
sample efficiency.

## Loss / objective
**Signals**: takes predictions + targets, returns a scalar (or vector that
reduces to a scalar).
**Naming**: `loss`, `objective`, `cost`, `score`, `reward`.
**Suggested metrics**: held-out task performance, calibration, robustness.

## Heuristic / scoring function
**Signals**: maps a state / candidate to a scalar judgement; used inside a
search loop. Often pure Python.
**Naming**: `heuristic`, `score`, `evaluate`, `rank`, `weight`.
**Suggested metrics**: search-loop end-to-end performance (final solution
quality, search depth required, expansions consumed).

## Game-agent policy
**Signals**: takes an observation/state dict and returns an action; called
by an external runner.
**Naming**: `act`, `agent`, `policy`, `play`, `move`.
**Suggested metrics**: head-to-head win rate vs. fixed opponent pool;
trajectory share. See `shinka-self-play`.

## Figure-generation function
**Signals**: writes an image to disk, takes an output path, uses
`matplotlib` / `cairosvg` / SVG strings.
**Naming**: `make_figure`, `plot`, `draw`, `render`.
**Suggested follow-up**: defer to the `shinka-create-figure` skill rather
than reporting a generic candidate; flag as `tier: figure` in the report.
