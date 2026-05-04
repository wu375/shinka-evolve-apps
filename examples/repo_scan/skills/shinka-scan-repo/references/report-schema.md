# Report Schema

The scanner produces two artifacts in the output directory: `report.json`
(machine-readable) and `report.md` (human summary).

## `report.json`

```json
{
  "schema_version": "1",
  "repo_path": "/abs/path/to/repo",
  "scanned_at": "2026-05-03T12:00:00Z",
  "files_scanned": 312,
  "functions_inspected": 1287,
  "functions_passed_filter": 84,
  "candidates": [
    {
      "id": "src/agent/policy.py::act",
      "file": "src/agent/policy.py",
      "lineno": 42,
      "qualname": "Policy.act",
      "tier": "high",                       // high | medium | low | figure | refused
      "combined_score": 0.78,
      "axes": {
        "self_containedness": 0.9,
        "determinism": 0.6,
        "measurable_output": 0.8,
        "improvability_surface": 0.9,
        "eval_cost": 0.7,
        "domain_pattern": 1.0
      },
      "domain_pattern": "game-agent-policy",
      "suggested_metrics": ["head-to-head win rate", "trajectory share"],
      "evaluator_sketch": "Reuse kaggle_orbit_war_examples/evaluator.py; pool of frozen prior versions.",
      "rationale": "Substantial algorithmic body, deterministic given seeded env, high-value metric available.",
      "extra_data": {
        "eval_seconds": 12,
        "evals_per_generation": 6,
        "llm_calls_per_generation": 3,
        "est_total_cost_usd_low": 4.5,
        "est_total_cost_usd_high": 38.0
      }
    }
  ]
}
```

## `report.md`

```
# ShinkaEvolve candidate scan

Repo: <repo_path>
Scanned: <files_scanned> files, <functions_inspected> functions, <functions_passed_filter> passed atomicity filter.

## Top candidates

### 1. src/agent/policy.py:42 — `Policy.act`  (combined 0.78, tier: high)
- Domain pattern: game-agent-policy
- Suggested metrics: head-to-head win rate, trajectory share
- Eval cost band: $4.5 – $38
- Rationale: <one paragraph>
- Next step: hand off to `shinka-self-play` (see `examples/self_play_game`).

(repeat for top N)
```

## Field Conventions

- `id` is `relative/path.py::QualName` — stable across reports.
- `tier`:
  - `high`: combined ≥ 0.75, atomicity passed, pattern matched.
  - `medium`: 0.6 ≤ combined < 0.75.
  - `low`: 0.4 ≤ combined < 0.6 (JSON-only; not rendered in markdown).
  - `figure`: matches figure-generation pattern (defer to
    `shinka-create-figure`).
- `evaluator_sketch` is short prose (1–3 sentences), not runnable code.
- All cost figures are USD bands; treat as order-of-magnitude.
