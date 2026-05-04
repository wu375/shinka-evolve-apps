# Kaggle Orbit War — Game-Specific Self-Play Package

This directory contains everything needed to run `shinka-self-play` on the
Kaggle **Orbit War** game. It includes a game runner, seed agent, baseline
bots, and a full robustness-oriented evaluator.

## Game Overview

Orbit War is a real-time strategy game for 2 or 4 players on a 100×100
continuous board with a sun at the center. Players start with a single home
planet and compete to control the map by sending fleets to capture neutral
and enemy planets. Planets orbit the sun, comets fly through on elliptical
trajectories, and fleets travel in straight lines. The game lasts 500 turns.
The player with the most total ships (on planets + in fleets) at the end wins.

Key mechanics:
- **Planets** orbit the sun or are static (depending on distance from center).
  Each planet produces 1–5 ships/turn when owned.
- **Fleets** travel in straight lines at speed scaling logarithmically with
  fleet size. Fleets crossing the sun are destroyed.
- **Comets** are temporary objects on elliptical paths that can be captured
  for extra production.
- **Combat** resolves when fleets collide with planets: largest attacker
  fights second-largest, then survivor fights the garrison.
- **Action format**: each turn the agent returns a list of
  `[from_planet_id, direction_angle, num_ships]` moves.

Agent interface: `agent(obs) -> list[list]` where obs contains planets,
fleets, angular_velocity, initial_planets, comets, comet_planet_ids, and
player ID. See `GAME_RULES.md` for the full specification.

## Files

- `README.md` — this file.
- `GAME_RULES.md` — complete game rules and observation/action reference.
- `game_runner.py` — `run_match` implementation using `kaggle_environments`.
- `evaluator.py` — full robustness-oriented evaluator with 2P and 4P
  match scheduling, trajectory analysis, and diagnostic metrics.
- `seed_agent.py` — starter agent for evolution (based on example0.py).
- `bots/example0.py` — baseline bot (simple nearest-planet-sniper style).
- `bots/example1.py` — stronger baseline bot (world-model based strategy).

## How to use

1. Copy the generic templates from `templates/` for the self-play loop:
   - `templates/evaluator_template.py` → `evaluate.py` (or use
     `evaluator.py` from this directory for the full evaluator)
   - `templates/run_self_play.py` → `scripts/run_self_play.py`
   - `templates/shinka_config_template.yaml` → `shinka.yaml`
2. Copy from this directory:
   - `game_runner.py` → `game_runner.py`
   - `seed_agent.py` → `initial.py`
   - `bots/*` → `bots/`
3. Add a `.env` with proposal-LLM API keys.
4. Smoke test, then run the self-play loop.

## Dependencies

- `kaggle_environments` with the `orbit_wars` environment registered.
