"""Robustness-oriented evaluator for orbit_wars agents.

The pool is loaded only from `bots/` so evaluation can use a curated mix of
hand-authored probe bots plus wrapped historical versions. The evaluator runs:

- lightweight 2P paired-color games versus every pool bot
- mixed 4P lobbies sampled from the same pool

Scoring blends:
- top-opponent trajectory share
- whole-field trajectory share
- production-share trajectory
- final top-opponent margin
- final placement
- a small speed bonus/penalty for decisive short games

The goal is to reward agents that survive a diverse meta, not just exploit a
small fixed clone pool.
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from kaggle_environments import make
from kaggle_environments.envs.orbit_wars.orbit_wars import (
    Planet,
    BOARD_SIZE,
    CENTER,
    SUN_RADIUS,
)

NUM_BINS = 10

# ---------- Evaluator knobs (tuned here; candidates should not override) ----------
# Default number of subprocess workers for parallel game evaluation. Kept low
# (2) so that overlapping evaluator runs on the same machine don't thrash the
# CPU and cause act-timeouts that break determinism. Override per-call via
# `evaluate(..., max_workers=N)`.
NUM_PARALLEL_WORKERS = 2

# 2P remains per-opponent but uses fewer seeds to stay fast as the pool grows.
SEEDS_PER_2P_OPPONENT = 1

# 4P is now evaluated separately via mixed lobbies sampled from the pool.
FOUR_PLAYER_MATCHES = 12

# Each pool bot should appear in at least this many mixed 4P lobbies. The
# scheduler keeps adding lobbies until both this coverage floor and
# `FOUR_PLAYER_MATCHES` are satisfied.
FOUR_PLAYER_MIN_APPEARANCES = 2

# Base seed feeding the per-game seed sampler. Fixed across candidates so that
# the same seed samples the same maps for head-to-head comparisons.
BASE_SEED = 1000

Agent = Callable[[dict], list] | str  # function or "random"


# ---------- opponent pool loaded from bots/ ----------

_HERE = os.path.dirname(os.path.abspath(__file__))
_BOTS_DIR = os.path.join(_HERE, "bots")
def _load_agent_from(path: str, mod_name: str) -> Callable:
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "agent"):
        raise AttributeError(f"{path} is missing `agent`")
    return module.agent


def _load_bot_agent(name: str) -> Callable:
    return _load_agent_from(os.path.join(_BOTS_DIR, f"{name}.py"), f"_bot_{name}")


DEFAULT_POOL_BOTS = (
    "example1",
    "example1_snipe",
    "example1_comet",
    "example1_swarm",
    "example1_turtle",
    "example1_pressure",
    "v9",
    "v56",
)


# Spec registry for parallel workers: name -> (kind, value) so each subprocess
# can rebuild the agent without pickling callables.
_DEFAULT_OPP_SPECS: dict[str, tuple[str, str]] = {
    name: ("bot", name) for name in DEFAULT_POOL_BOTS
}


def _build_agent_from_spec(spec: tuple[str, str]) -> Agent:
    kind, val = spec
    if kind == "builtin":
        return val
    if kind == "bot":
        return _load_bot_agent(val)
    if kind == "path":
        mod_name = "_cand_" + os.path.splitext(os.path.basename(val))[0]
        return _load_agent_from(val, mod_name)
    raise ValueError(f"Unknown spec kind: {kind}")


def _default_opponents() -> dict[str, Agent]:
    return {name: _build_agent_from_spec(spec) for name, spec in _DEFAULT_OPP_SPECS.items()}


# Lazy-built so importing this module doesn't fail if bots/ has a syntax error
# in one file.
DEFAULT_OPPONENTS: dict[str, Agent] = _default_opponents()


def nearest_planet_sniper(obs):
    """Kept around as a weak reference bot / smoke test."""
    moves = []
    player = obs.get("player", 0) if isinstance(obs, dict) else obs.player
    raw_planets = obs.get("planets", []) if isinstance(obs, dict) else obs.planets
    planets = [Planet(*p) for p in raw_planets]
    my = [p for p in planets if p.owner == player]
    targets = [p for p in planets if p.owner != player]
    if not targets:
        return moves
    for mine in my:
        nearest = min(targets, key=lambda t: (mine.x - t.x) ** 2 + (mine.y - t.y) ** 2)
        ships_needed = max(nearest.ships + 1, 20)
        if mine.ships >= ships_needed:
            angle = math.atan2(nearest.y - mine.y, nearest.x - mine.x)
            moves.append([mine.id, angle, ships_needed])
    return moves


@dataclass(init=False)
class EvalConfig:
    opponents: dict[str, Agent] = field(default_factory=_default_opponents)
    seeds_per_2p_opponent: int = field(default_factory=lambda: SEEDS_PER_2P_OPPONENT)
    four_player_matches: int = field(default_factory=lambda: FOUR_PLAYER_MATCHES)
    four_player_min_appearances: int = field(
        default_factory=lambda: FOUR_PLAYER_MIN_APPEARANCES
    )
    play_2p: bool = True
    play_4p: bool = True
    swap_2p_colors: bool = True
    rotate_4p_slots: bool = True
    base_seed: int = field(default_factory=lambda: BASE_SEED)

    def __init__(
        self,
        opponents: dict[str, Agent] | None = None,
        seeds_per_2p_opponent: int | None = None,
        four_player_matches: int | None = None,
        four_player_min_appearances: int | None = None,
        play_2p: bool = True,
        play_4p: bool = True,
        swap_2p_colors: bool = True,
        rotate_4p_slots: bool = True,
        base_seed: int | None = None,
        seeds_per_opponent: int | None = None,
    ) -> None:
        # Backward-compatibility shim for older callers that still pass the old
        # evaluator keyword. It now aliases to the lighter 2P-per-opponent seed count.
        if seeds_per_2p_opponent is None and seeds_per_opponent is not None:
            seeds_per_2p_opponent = seeds_per_opponent

        self.opponents = opponents if opponents is not None else _default_opponents()
        self.seeds_per_2p_opponent = (
            int(seeds_per_2p_opponent)
            if seeds_per_2p_opponent is not None
            else SEEDS_PER_2P_OPPONENT
        )
        self.four_player_matches = (
            int(four_player_matches)
            if four_player_matches is not None
            else FOUR_PLAYER_MATCHES
        )
        self.four_player_min_appearances = (
            int(four_player_min_appearances)
            if four_player_min_appearances is not None
            else FOUR_PLAYER_MIN_APPEARANCES
        )
        self.play_2p = bool(play_2p)
        self.play_4p = bool(play_4p)
        self.swap_2p_colors = bool(swap_2p_colors)
        self.rotate_4p_slots = bool(rotate_4p_slots)
        self.base_seed = int(base_seed) if base_seed is not None else BASE_SEED


# ---------- game runner ----------

def _bin_index(step_idx: int, num_steps: int) -> int:
    if num_steps <= 1:
        return 0
    return min(NUM_BINS - 1, step_idx * NUM_BINS // num_steps)


def _classify_fleet_death(x: float, y: float, planets_last) -> str:
    """Rough classification of why a fleet vanished between this step and the next.
    `planets_last` is the planet list at the step the fleet was last seen."""
    if x < 0 or x > BOARD_SIZE or y < 0 or y > BOARD_SIZE:
        return "oob"
    if math.hypot(x - CENTER, y - CENTER) < SUN_RADIUS + 2.0:
        return "sun"
    for p in planets_last:
        _id, _o, px, py, pr, _s, _prod = p
        if math.hypot(x - px, y - py) < pr + 2.0:
            return "combat"
    return "expired"


def _classify_curve(binned_share: list[float]) -> str:
    """Classify trajectory-share shape. Assumes 10 bins, values in [0,1]."""
    if not binned_share:
        return "unknown"
    peak = max(binned_share)
    peak_i = binned_share.index(peak)
    first, last = binned_share[0], binned_share[-1]
    mean = sum(binned_share) / len(binned_share)
    if mean > 0.7 and last > 0.6:
        return "dominant"
    if peak > 0.65 and last < 0.25 and peak_i <= 6:
        return "peak_then_collapse"
    if first > 0.4 and last < first - 0.2:
        return "steady_decline"
    if mean < 0.35 and last < 0.35:
        return "always_behind"
    if last > first + 0.2 and last > 0.55:
        return "late_comeback"
    return "close"


def _normalized_share(share: float, num_players: int) -> float:
    if num_players <= 1:
        return 0.0
    return max(-1.0, min(1.0, ((share * num_players) - 1.0) / (num_players - 1)))


def _compute_game_metrics(env, candidate_slot: int, num_players: int) -> dict[str, Any]:
    """Single pass over env.steps producing per-game metrics + diagnostics.
    Scoring fields: trajectory_share, endpoint margin/log-ratio, speed bonus, composite.
    Diagnostics fields: 10-bin trends, curve shape, pivotal turn, ownership transitions,
    fleet death breakdown, rejected launches, home-planet lost turn.
    """
    steps = env.steps
    num_steps = len(steps)
    traj_shares_top: list[float] = []
    traj_shares_field: list[float] = []
    traj_prod_shares: list[float] = []
    planets_held_over_time: list[int] = []
    ships_over_time: list[int] = []
    prod_over_time: list[int] = []
    prev_fleet_ids: set = set()
    total_fleets_launched = 0
    total_launch_ships = 0
    elimination_turn: int | None = None
    lead_turns = 0
    prod_lead_turns = 0

    # Per-bin trend accumulators
    zero_bins = lambda: [0] * NUM_BINS  # noqa: E731
    bin_counts = zero_bins()
    share_top_bin_sum = [0.0] * NUM_BINS
    share_field_bin_sum = [0.0] * NUM_BINS
    share_prod_bin_sum = [0.0] * NUM_BINS
    my_planets_bin_sum = zero_bins()
    my_ships_bin_sum = zero_bins()
    my_prod_bin_sum = zero_bins()
    opp_top_bin_sum = zero_bins()
    captures_by_bin = zero_bins()
    losses_by_bin = zero_bins()
    launches_by_bin = zero_bins()
    launch_ships_by_bin = zero_bins()
    rejected_by_bin = zero_bins()

    # Ownership transition tracking
    prev_owner: dict[int, int] = {}
    churn_planets: set[int] = set()
    total_captures = 0
    total_losses = 0
    neutral_captures = 0
    enemy_captures = 0
    neutral_losses = 0
    enemy_losses = 0
    home_planet_ids: set[int] = set()
    home_lost_turn: int | None = None

    # Fleet lifecycle tracking (last seen position + planet list)
    last_fleet_state: dict[int, tuple[float, float, int, int]] = {}
    # id -> (x, y, owner, step_idx_last_seen)
    last_planets_snapshot = None
    fleet_deaths = {"sun": 0, "oob": 0, "combat": 0, "expired": 0}
    my_fleet_deaths = {"sun": 0, "oob": 0, "combat": 0, "expired": 0}

    # Comet grabs: planets with production=0 (comets) captured by candidate
    comet_grabs = 0

    final_ships = [0] * num_players
    for step_idx, step in enumerate(steps):
        obs = step[0]["observation"]
        bi = _bin_index(step_idx, num_steps)
        ships_by_owner = [0] * num_players
        held = 0
        my_prod = 0
        cur_planet_owner: dict[int, int] = {}
        cur_planet_prod: dict[int, int] = {}
        comet_ids = set(obs.get("comet_planet_ids", []) or [])
        for p in obs["planets"]:
            _id, owner, _x, _y, _r, s, _prod = p
            cur_planet_owner[_id] = owner
            cur_planet_prod[_id] = _prod
            if 0 <= owner < num_players:
                ships_by_owner[owner] += s
                if owner == candidate_slot:
                    held += 1
                    my_prod += _prod
        for f in obs.get("fleets", []):
            _id, owner, _x, _y, _a, _src, s = f
            if 0 <= owner < num_players:
                ships_by_owner[owner] += s

        # Record initial home planets on step 1 (step 0 may be pre-init).
        if step_idx == 1 and not home_planet_ids:
            home_planet_ids = {pid for pid, o in cur_planet_owner.items() if o == candidate_slot}

        # Ownership transitions vs previous step
        for pid, cur_o in cur_planet_owner.items():
            prev_o = prev_owner.get(pid)
            if prev_o is not None and prev_o != cur_o:
                churn_planets.add(pid)
                if cur_o == candidate_slot and prev_o != candidate_slot:
                    total_captures += 1
                    captures_by_bin[bi] += 1
                    if prev_o == -1:
                        neutral_captures += 1
                    else:
                        enemy_captures += 1
                    if pid in comet_ids:
                        comet_grabs += 1
                elif prev_o == candidate_slot and cur_o != candidate_slot:
                    total_losses += 1
                    losses_by_bin[bi] += 1
                    if cur_o == -1:
                        neutral_losses += 1
                    else:
                        enemy_losses += 1
                    if pid in home_planet_ids and home_lost_turn is None:
                        home_lost_turn = step_idx
        prev_owner = cur_planet_owner

        mine = ships_by_owner[candidate_slot]
        top_opp = max(
            (s for i, s in enumerate(ships_by_owner) if i != candidate_slot),
            default=0,
        )
        denom = mine + top_opp
        share_top = 0.5 if denom <= 0 else mine / denom
        total_ships_all = sum(ships_by_owner)
        share_field = (1.0 / num_players) if total_ships_all <= 0 else mine / total_ships_all
        prod_by_owner = [0] * num_players
        for p in obs["planets"]:
            owner = p[1]
            prod = p[6]
            if 0 <= owner < num_players:
                prod_by_owner[owner] += prod
        total_prod_all = sum(prod_by_owner)
        share_prod = (1.0 / num_players) if total_prod_all <= 0 else prod_by_owner[candidate_slot] / total_prod_all

        traj_shares_top.append(share_top)
        traj_shares_field.append(share_field)
        traj_prod_shares.append(share_prod)
        if mine == max(ships_by_owner) and mine > 0:
            lead_turns += 1
        if prod_by_owner[candidate_slot] == max(prod_by_owner) and prod_by_owner[candidate_slot] > 0:
            prod_lead_turns += 1

        planets_held_over_time.append(held)
        ships_over_time.append(mine)
        prod_over_time.append(my_prod)
        if elimination_turn is None and mine <= 0 and held <= 0:
            elimination_turn = step_idx

        bin_counts[bi] += 1
        share_top_bin_sum[bi] += share_top
        share_field_bin_sum[bi] += share_field
        share_prod_bin_sum[bi] += share_prod
        my_planets_bin_sum[bi] += held
        my_ships_bin_sum[bi] += mine
        my_prod_bin_sum[bi] += my_prod
        opp_top_bin_sum[bi] += top_opp

        # Launch tracking + fleet lifecycle
        cur_fleets_all: dict[int, tuple[float, float, int]] = {
            f[0]: (f[2], f[3], f[1]) for f in obs.get("fleets", [])
        }
        cur_ids_all = set(cur_fleets_all.keys())
        cur_my_ids = {fid for fid, (_, _, o) in cur_fleets_all.items() if o == candidate_slot}
        new_my_ids = cur_my_ids - prev_fleet_ids
        total_fleets_launched += len(new_my_ids)
        launches_by_bin[bi] += len(new_my_ids)
        prev_fleet_ids = cur_my_ids

        # Dead fleets (in last_fleet_state but not in current)
        dead_ids = set(last_fleet_state.keys()) - cur_ids_all
        for dfid in dead_ids:
            lx, ly, lo, _lstep = last_fleet_state[dfid]
            cause = _classify_fleet_death(lx, ly, last_planets_snapshot or obs["planets"])
            fleet_deaths[cause] += 1
            if lo == candidate_slot:
                my_fleet_deaths[cause] += 1
            del last_fleet_state[dfid]
        # Update last_fleet_state with current survivors
        for fid, (fx, fy, fo) in cur_fleets_all.items():
            last_fleet_state[fid] = (fx, fy, fo, step_idx)
        last_planets_snapshot = obs["planets"]

        # Rejected launches: submitted actions that did not produce a new fleet.
        # Obs at step N reflects the post-action state of step N, so fleets from
        # action[N] appear in new_my_ids at step N.
        action = step[candidate_slot].get("action") if len(step) > candidate_slot else None
        submitted = 0
        if isinstance(action, list):
            for mv in action:
                try:
                    _fpid, _ang, nships = int(mv[0]), float(mv[1]), int(mv[2])
                    if nships > 0:
                        submitted += 1
                        total_launch_ships += nships
                        launch_ships_by_bin[bi] += nships
                except Exception:
                    pass
        rejected_by_bin[bi] += max(0, submitted - len(new_my_ids))

        if step_idx == num_steps - 1:
            final_ships = ships_by_owner

    mine_f = final_ships[candidate_slot]
    top_f = max(s for i, s in enumerate(final_ships) if i != candidate_slot)
    winner = (
        max(range(num_players), key=lambda i: final_ships[i])
        if sum(final_ships) > 0
        else -1
    )
    win = int(winner == candidate_slot)
    final_rank = 1 + sum(1 for s in final_ships if s > mine_f)

    # Endpoint margin (old interpretable metric)
    endpoint_margin = 0.0 if (mine_f + top_f) <= 0 else (mine_f - top_f) / (mine_f + top_f)
    # Smoothed log-ratio — differentiates 3000-vs-0 from 800-vs-0.
    final_log_ratio = math.tanh(0.5 * math.log((mine_f + 1) / (top_f + 1)))
    # Trajectory dominance against the strongest opponent.
    trajectory_share_top = sum(traj_shares_top) / len(traj_shares_top) if traj_shares_top else 0.5
    trajectory_share_field = (
        sum(traj_shares_field) / len(traj_shares_field) if traj_shares_field else (1.0 / num_players)
    )
    trajectory_prod_share = (
        sum(traj_prod_shares) / len(traj_prod_shares) if traj_prod_shares else (1.0 / num_players)
    )
    rank_score = 1.0 - 2.0 * (final_rank - 1) / max(1, num_players - 1)
    # Signed speed bonus: short games are amplified only if decisive.
    speed_factor = (500 - num_steps) / 500.0  # in [0, 1); ~0 if went full length
    if win and mine_f > top_f:
        speed_bonus = speed_factor
    elif final_rank == num_players and mine_f < top_f:
        speed_bonus = -speed_factor
    else:
        speed_bonus = 0.0

    composite = (
        0.35 * (2 * trajectory_share_top - 1)
        + 0.15 * _normalized_share(trajectory_share_field, num_players)
        + 0.15 * _normalized_share(trajectory_prod_share, num_players)
        + 0.15 * final_log_ratio
        + 0.15 * rank_score
        + 0.05 * speed_bonus
    )
    composite = max(-1.0, min(1.0, composite))

    peak_ships = max(ships_over_time) if ships_over_time else 0
    peak_planets = max(planets_held_over_time) if planets_held_over_time else 0
    final_planets = planets_held_over_time[-1] if planets_held_over_time else 0
    time_to_peak_planets = (
        planets_held_over_time.index(peak_planets) if peak_planets else 0
    )

    stats = {
        "game_length": num_steps,
        "fleets_launched": total_fleets_launched,
        "launch_ships": total_launch_ships,
        "peak_ships": peak_ships,
        "final_ships": mine_f,
        "peak_planets": peak_planets,
        "final_planets": final_planets,
        "time_to_peak_planets": time_to_peak_planets,
        "collapsed": int(final_planets == 0),
        "trajectory_share_top": trajectory_share_top,
        "trajectory_share_field": trajectory_share_field,
        "trajectory_prod_share": trajectory_prod_share,
        "final_log_ratio": final_log_ratio,
        "final_rank": final_rank,
        "rank_score": rank_score,
        "survived": int(final_planets > 0 or mine_f > 0),
        "elimination_turn": elimination_turn if elimination_turn is not None else num_steps,
        "lead_turn_rate": lead_turns / max(1, num_steps),
        "prod_lead_turn_rate": prod_lead_turns / max(1, num_steps),
        "speed_bonus": speed_bonus,
    }

    # Binned means (normalize sums by counts)
    def _avg(sums, counts):
        return [round(sums[i] / counts[i], 3) if counts[i] else 0.0 for i in range(NUM_BINS)]

    share_top_binned = _avg(share_top_bin_sum, bin_counts)
    # Pivotal turn: largest 3-bin drop in trajectory share
    pivotal_turn = 0
    pivotal_drop = 0.0
    for i in range(NUM_BINS - 3):
        drop = share_top_binned[i] - share_top_binned[i + 3]
        if drop > pivotal_drop:
            pivotal_drop = drop
            # turn = start of the falling window
            pivotal_turn = int((i + 1) * num_steps / NUM_BINS)

    diagnostics = {
        "curve_shape": _classify_curve(share_top_binned),
        "trajectory_share_top_binned": share_top_binned,
        "trajectory_share_field_binned": _avg(share_field_bin_sum, bin_counts),
        "trajectory_prod_share_binned": _avg(share_prod_bin_sum, bin_counts),
        "my_planets_binned": _avg(my_planets_bin_sum, bin_counts),
        "my_ships_binned": _avg(my_ships_bin_sum, bin_counts),
        "my_prod_binned": _avg(my_prod_bin_sum, bin_counts),
        "opp_top_ships_binned": _avg(opp_top_bin_sum, bin_counts),
        "captures_by_bin": captures_by_bin,
        "losses_by_bin": losses_by_bin,
        "launches_by_bin": launches_by_bin,
        "launch_ships_by_bin": launch_ships_by_bin,
        "rejected_launches_by_bin": rejected_by_bin,
        "total_captures": total_captures,
        "total_losses": total_losses,
        "neutral_captures": neutral_captures,
        "enemy_captures": enemy_captures,
        "neutral_losses": neutral_losses,
        "enemy_losses": enemy_losses,
        "churn_planets": len(churn_planets),
        "home_lost_turn": home_lost_turn,
        "comet_grabs": comet_grabs,
        "fleet_deaths": fleet_deaths,
        "my_fleet_deaths": my_fleet_deaths,
        "pivotal_turn": pivotal_turn,
        "pivotal_drop": round(pivotal_drop, 3),
    }

    return {
        "final_ships": final_ships,
        "winner": winner,
        "win": win,
        "margin": endpoint_margin,
        "composite": composite,
        "stats": stats,
        "diagnostics": diagnostics,
    }


STEP_BUDGET_S = 1.0


def _wrap_with_timing(agent: Agent, durations: list[float]) -> Agent:
    """Wrap a callable agent so each call's duration is appended to `durations`.
    Returns string agents unchanged (kaggle-builtins like 'random' run in-engine)."""
    if not callable(agent):
        return agent

    def timed(obs):
        t0 = time.perf_counter()
        try:
            return agent(obs)
        finally:
            durations.append(time.perf_counter() - t0)

    return timed


def _run_game(
    agents: list[Agent], seed: int, num_players: int, candidate_slot: int
) -> dict[str, Any]:
    # NOTE: kaggle's orbit_wars env calls random.randint/uniform at interpret()
    # time without ever seeding the random module. `configuration["seed"]` is
    # ignored for map generation. Seed the global Python `random` module here
    # so that the same (seed, num_players) pair produces the same map.
    random.seed(seed)
    env = make(
        "orbit_wars",
        configuration={"seed": seed, "agents": num_players},
        debug=False,
    )
    step_durations: list[float] = []
    timed_agents = list(agents)
    timed_agents[candidate_slot] = _wrap_with_timing(
        agents[candidate_slot], step_durations
    )
    t0 = time.perf_counter()
    env.run(timed_agents)
    elapsed = time.perf_counter() - t0
    metrics = _compute_game_metrics(env, candidate_slot, num_players)

    # Step-time stats
    if step_durations:
        srt = sorted(step_durations)
        n = len(srt)
        p95 = srt[min(n - 1, int(0.95 * n))]
        max_s = srt[-1]
        mean_s = sum(srt) / n
        over_budget = sum(1 for d in step_durations if d > STEP_BUDGET_S)
    else:
        p95 = max_s = mean_s = 0.0
        over_budget = 0
    step_stats = {
        "step_count": len(step_durations),
        "step_mean_s": mean_s,
        "step_p95_s": p95,
        "step_max_s": max_s,
        "step_over_budget": over_budget,
    }
    return {
        "seed": seed,
        "num_players": num_players,
        "candidate_slot": candidate_slot,
        "wallclock_s": elapsed,
        "step_stats": step_stats,
        **metrics,
    }


# ---------- evaluator ----------

def _resolve_candidate_spec(candidate: Agent) -> tuple[str, str] | None:
    """Build a (kind, value) spec that a worker can use to rebuild the agent.
    Returns None if the candidate can't be parallelized (e.g. lambda)."""
    if isinstance(candidate, str):
        return ("builtin", candidate)
    try:
        path = inspect.getfile(candidate)
    except TypeError:
        return None
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return None
    return ("path", path)


def _run_game_task(
    cand_spec: tuple[str, str],
    opp_specs: list[tuple[str, str]],
    seed: int,
    num_players: int,
    candidate_slot: int,
) -> dict[str, Any]:
    """Worker entry point. Executed inside a subprocess: loads agents,
    runs one game, returns the same dict shape as _run_game()."""
    cand = _build_agent_from_spec(cand_spec)
    opps = [_build_agent_from_spec(spec) for spec in opp_specs]
    agents: list[Agent] = []
    oi = 0
    for slot in range(num_players):
        if slot == candidate_slot:
            agents.append(cand)
        else:
            agents.append(opps[oi])
            oi += 1
    return _run_game(agents, seed, num_players, candidate_slot)


def _build_game_plan(cfg: EvalConfig) -> list[dict[str, Any]]:
    """Produce the full list of games to run (seeds pre-sampled deterministically).
    Each entry carries the metadata that must be merged into the result."""
    rng = random.Random(cfg.base_seed)
    tasks: list[dict[str, Any]] = []
    opp_names = list(cfg.opponents.keys())
    if cfg.play_2p:
        for opp_name in opp_names:
            for _ in range(cfg.seeds_per_2p_opponent):
                seed = rng.randint(0, 2**31 - 1)
                tasks.append({
                    "opponent": opp_name,
                    "opponents": [opp_name],
                    "seed": seed,
                    "num_players": 2,
                    "candidate_slot": 0,
                    "mode": "2p",
                    "swap": False,
                })
                if cfg.swap_2p_colors:
                    tasks.append({
                        "opponent": opp_name,
                        "opponents": [opp_name],
                        "seed": seed,
                        "num_players": 2,
                        "candidate_slot": 1,
                        "mode": "2p",
                        "swap": True,
                    })
    if cfg.play_4p and opp_names:
        coverage = {name: 0 for name in opp_names}
        target_matches = max(
            cfg.four_player_matches,
            math.ceil(
                max(0, cfg.four_player_min_appearances) * len(opp_names) / 3
            ),
        )
        idx = 0
        while idx < target_matches or min(coverage.values(), default=0) < cfg.four_player_min_appearances:
            seed = rng.randint(0, 2**31 - 1)
            if len(opp_names) >= 3:
                needed = [
                    name for name in opp_names
                    if coverage[name] < cfg.four_player_min_appearances
                ]
                if len(needed) >= 3:
                    pool = needed
                elif needed:
                    remaining = [name for name in opp_names if name not in needed]
                    remaining.sort(key=lambda name: (coverage[name], rng.random()))
                    pool = needed + remaining[: 3 - len(needed)]
                else:
                    pool = list(opp_names)
                pool = sorted(pool, key=lambda name: (coverage[name], rng.random()))
                lobby = pool[:3]
            else:
                ranked = sorted(opp_names, key=lambda name: (coverage[name], rng.random()))
                lobby = [ranked[i % len(ranked)] for i in range(3)]

            for name in lobby:
                coverage[name] += 1
            tasks.append({
                "opponent": "+".join(sorted(lobby)),
                "opponents": lobby,
                "seed": seed,
                "num_players": 4,
                "candidate_slot": (idx % 4) if cfg.rotate_4p_slots else 0,
                "mode": "4p",
                "swap": False,
            })
            idx += 1
    return tasks


def evaluate(
    candidate: Agent,
    config: EvalConfig | None = None,
    parallel: bool | None = None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    cfg = config or EvalConfig()
    tasks = _build_game_plan(cfg)
    per_game: list[dict[str, Any]] = []

    # Decide parallel vs serial. Parallel requires every agent to have a spec
    # (a valid file path for candidate and loadable bot specs for opponents).
    cand_spec = _resolve_candidate_spec(candidate)
    opp_spec_by_name = {name: _DEFAULT_OPP_SPECS.get(name) for name in cfg.opponents.keys()}
    can_parallel = cand_spec is not None and all(s is not None for s in opp_spec_by_name.values())
    if parallel is None:
        parallel = can_parallel
    if parallel and not can_parallel:
        raise RuntimeError("Cannot parallelize: candidate or opponent spec missing.")

    if parallel:
        workers = max_workers or NUM_PARALLEL_WORKERS
        with ProcessPoolExecutor(max_workers=workers) as pool:
            fut_to_meta = {}
            for t in tasks:
                fut = pool.submit(
                    _run_game_task,
                    cand_spec,
                    [opp_spec_by_name[name] for name in t["opponents"]],
                    t["seed"],
                    t["num_players"],
                    t["candidate_slot"],
                )
                fut_to_meta[fut] = t
            for fut in as_completed(fut_to_meta):
                r = fut.result()
                t = fut_to_meta[fut]
                r.update({
                    "opponent": t["opponent"],
                    "opponents": list(t["opponents"]),
                    "mode": t["mode"],
                    "swap": t["swap"],
                    "candidate_slot": t["candidate_slot"],
                })
                per_game.append(r)
    else:
        # Serial path: reuse in-process opponent callables (needed for custom
        # non-path agents passed by the user).
        opp_by_name = dict(cfg.opponents)
        for t in tasks:
            agents: list[Agent] = []
            oi = 0
            for slot in range(t["num_players"]):
                if slot == t["candidate_slot"]:
                    agents.append(candidate)
                else:
                    agents.append(opp_by_name[t["opponents"][oi]])
                    oi += 1
            r = _run_game(agents, t["seed"], t["num_players"], t["candidate_slot"])
            r.update({
                "opponent": t["opponent"],
                "opponents": list(t["opponents"]),
                "mode": t["mode"],
                "swap": t["swap"],
                "candidate_slot": t["candidate_slot"],
            })
            per_game.append(r)

    return _summarize(per_game, cfg)


def _agg(games: list[dict]) -> dict[str, float]:
    if not games:
        return {
            "games": 0, "win_rate": 0.0,
            "avg_composite": 0.0, "composite_stdev": 0.0,
            "avg_margin": 0.0, "avg_trajectory_share": 0.5,
            "avg_field_share": 0.0, "avg_prod_share": 0.0,
            "avg_rank": 0.0, "survival_rate": 0.0,
        }
    composites = [g["composite"] for g in games]
    margins = [g["margin"] for g in games]
    trajs = [g["stats"]["trajectory_share_top"] for g in games]
    field_trajs = [g["stats"]["trajectory_share_field"] for g in games]
    prod_trajs = [g["stats"]["trajectory_prod_share"] for g in games]
    ranks = [g["stats"]["final_rank"] for g in games]
    survival = [g["stats"]["survived"] for g in games]
    return {
        "games": len(games),
        "win_rate": sum(g["win"] for g in games) / len(games),
        "avg_composite": statistics.mean(composites),
        "composite_stdev": statistics.pstdev(composites) if len(composites) > 1 else 0.0,
        "avg_margin": statistics.mean(margins),
        "avg_trajectory_share": statistics.mean(trajs),
        "avg_field_share": statistics.mean(field_trajs),
        "avg_prod_share": statistics.mean(prod_trajs),
        "avg_rank": statistics.mean(ranks),
        "survival_rate": statistics.mean(survival),
    }


def _opponent_cluster(name: str) -> str:
    lowered = name.lower()
    if "comet" in lowered:
        return "comet_tempo"
    if "swarm" in lowered:
        return "swarm_pressure"
    if "turtle" in lowered:
        return "slow_burn_turtle"
    if "pressure" in lowered:
        return "hostile_pressure"
    if "snipe" in lowered:
        return "snipe_timing"
    if lowered.startswith("v"):
        return "historical_selfplay"
    if "example" in lowered:
        return "balanced_reference"
    return "misc"


def _round_agg(agg: dict[str, float]) -> dict[str, float]:
    keys = (
        "games",
        "win_rate",
        "avg_composite",
        "avg_margin",
        "avg_trajectory_share",
        "avg_field_share",
        "avg_prod_share",
        "avg_rank",
        "survival_rate",
    )
    out = {}
    for key in keys:
        val = agg.get(key)
        if isinstance(val, float):
            out[key] = round(val, 3)
        else:
            out[key] = val
    return out


def _cluster_agg(games: list[dict], mode: str) -> dict[str, dict[str, float]]:
    by_cluster: dict[str, list[dict]] = {}
    for game in games:
        if mode == "2p":
            clusters = [_opponent_cluster(game["opponent"])]
        else:
            clusters = sorted({_opponent_cluster(name) for name in game.get("opponents", [])})
        for cluster in clusters:
            by_cluster.setdefault(cluster, []).append(game)
    return {
        cluster: _round_agg(_agg(cluster_games))
        for cluster, cluster_games in sorted(by_cluster.items())
    }


def _build_mutator_public(
    overall: dict[str, dict],
    diagnostics: dict[str, Any],
    timing: dict[str, Any],
    games_2p: list[dict],
    games_4p: list[dict],
    four_player_presence_counts: dict[str, int],
) -> dict[str, Any]:
    """Compact LLM-facing metrics.

    The EA optimizes `combined_score`; this dict is only explanatory context for
    mutation prompts, so keep it concise and avoid exposing exact pool identities.
    Detailed raw diagnostics stay in `private`.
    """
    hardest_2p = sorted(games_2p, key=lambda g: g["composite"])[:3]
    hardest_4p = sorted(games_4p, key=lambda g: g["composite"])[:3]
    home_lost_rate = diagnostics.get("home_lost_rate", 0.0)
    place_counts = diagnostics.get("place_counts", {})
    curve_shapes = diagnostics.get("curve_shape_counts", {})

    guidance = []
    if home_lost_rate >= 0.45:
        guidance.append("High home-loss rate: improve early/frontier reserve and avoid draining threatened sources.")
    if place_counts.get("3", 0) or place_counts.get("4", 0):
        guidance.append("Some low 4P placements: improve mixed-lobby robustness and avoid visible overextension.")
    if curve_shapes.get("peak_then_collapse", 0) or curve_shapes.get("steady_decline", 0):
        guidance.append("Trajectory collapse/decline appears: prioritize retention, defense, and production durability.")
    if timing.get("budget_violated"):
        guidance.append("Runtime budget violated: simplify search or reduce expensive per-turn logic.")
    if not guidance:
        guidance.append("No single dominant failure signal; search for matchup-robust value/sizing improvements.")

    def _compact_worst(game: dict) -> dict[str, Any]:
        return {
            "mode": game["mode"],
            "opponent_cluster": (
                _opponent_cluster(game["opponent"]) if game["mode"] == "2p"
                else sorted({_opponent_cluster(name) for name in game.get("opponents", [])})
            ),
            "composite": round(game["composite"], 3),
            "curve_shape": game["diagnostics"]["curve_shape"],
            "home_lost_turn": game["diagnostics"]["home_lost_turn"],
            "trajectory_top_bins": game["diagnostics"]["trajectory_share_top_binned"],
            "captures": sum(game["diagnostics"]["captures_by_bin"]),
            "losses": sum(game["diagnostics"]["losses_by_bin"]),
        }

    return {
        "score_summary": {
            "2p": _round_agg(overall["2p"]),
            "4p": _round_agg(overall["4p"]),
            "all": _round_agg(overall["all"]),
            "worst_mode_composite": round(min(
                overall["2p"]["avg_composite"],
                overall["4p"]["avg_composite"],
            ), 3),
        },
        "cluster_summary": {
            "2p": _cluster_agg(games_2p, "2p"),
            "4p_presence": _cluster_agg(games_4p, "4p"),
        },
        "failure_signals": {
            "home_lost_rate": home_lost_rate,
            "place_counts": place_counts,
            "curve_shape_counts": curve_shapes,
            "worst_2p_games": [_compact_worst(g) for g in hardest_2p],
            "worst_4p_games": [_compact_worst(g) for g in hardest_4p],
        },
        "coverage": {
            "four_player_presence_min": min(four_player_presence_counts.values(), default=0),
            "four_player_presence_max": max(four_player_presence_counts.values(), default=0),
        },
        "step_timing": timing,
        "mutation_guidance": guidance,
    }


def _summarize(per_game: list[dict], cfg: EvalConfig) -> dict[str, Any]:
    games_2p = [g for g in per_game if g["mode"] == "2p"]
    games_4p = [g for g in per_game if g["mode"] == "4p"]

    overall = {"2p": _agg(games_2p), "4p": _agg(games_4p), "all": _agg(per_game)}

    by_opponent_2p = {
        opp: _agg([g for g in games_2p if g["opponent"] == opp])
        for opp in cfg.opponents.keys()
    }
    by_opponent_4p_presence = {
        opp: _agg([g for g in games_4p if opp in g.get("opponents", [])])
        for opp in cfg.opponents.keys()
    }
    four_player_presence_counts = {
        opp: sum(1 for g in games_4p if opp in g.get("opponents", []))
        for opp in cfg.opponents.keys()
    }
    by_4p_seat = {
        str(slot): _agg([g for g in games_4p if g.get("candidate_slot") == slot])
        for slot in range(4)
    }

    stat_keys = per_game[0]["stats"].keys() if per_game else []
    stat_means = {
        k: statistics.mean(g["stats"][k] for g in per_game) for k in stat_keys
    }

    # Combined score: mean of mode avg_composite — penalizes specialists.
    mode_avgs = []
    if cfg.play_2p and games_2p:
        mode_avgs.append(overall["2p"]["avg_composite"])
    if cfg.play_4p and games_4p:
        mode_avgs.append(overall["4p"]["avg_composite"])
    combined = float(statistics.mean(mode_avgs)) if mode_avgs else 0.0
    worst_mode = float(min(mode_avgs)) if mode_avgs else 0.0

    diagnostics = _aggregate_diagnostics(per_game)

    # Step-timing aggregates (across all games)
    all_max = [g["step_stats"]["step_max_s"] for g in per_game]
    all_p95 = [g["step_stats"]["step_p95_s"] for g in per_game]
    all_mean = [g["step_stats"]["step_mean_s"] for g in per_game]
    total_over = sum(g["step_stats"]["step_over_budget"] for g in per_game)
    total_steps = sum(g["step_stats"]["step_count"] for g in per_game)
    worst_step = max(all_max) if all_max else 0.0
    timing = {
        "step_budget_s": STEP_BUDGET_S,
        "worst_step_s_across_games": round(worst_step, 4),
        "mean_p95_step_s": round(statistics.mean(all_p95), 4) if all_p95 else 0.0,
        "mean_step_s": round(statistics.mean(all_mean), 4) if all_mean else 0.0,
        "steps_over_budget_total": total_over,
        "steps_total": total_steps,
        "budget_violated": worst_step > STEP_BUDGET_S,
    }

    detailed_public = {
        "overall": overall,
        "by_opponent_2p": by_opponent_2p,
        "by_opponent_4p_presence": by_opponent_4p_presence,
        "four_player_presence_counts": four_player_presence_counts,
        "by_4p_seat": by_4p_seat,
        "behavioral_stats": stat_means,
        "worst_mode_composite": worst_mode,
        "step_timing": timing,
        "diagnostics": diagnostics,
    }
    public = _build_mutator_public(
        overall,
        diagnostics,
        timing,
        games_2p,
        games_4p,
        four_player_presence_counts,
    )
    private = {
        "per_game": per_game,
        "detailed_public": detailed_public,
        "config": {
            "opponents": list(cfg.opponents.keys()),
            "seeds_per_2p_opponent": cfg.seeds_per_2p_opponent,
            "four_player_matches": cfg.four_player_matches,
            "four_player_min_appearances": cfg.four_player_min_appearances,
            "play_2p": cfg.play_2p,
            "play_4p": cfg.play_4p,
            "swap_2p_colors": cfg.swap_2p_colors,
            "rotate_4p_slots": cfg.rotate_4p_slots,
            "base_seed": cfg.base_seed,
        },
    }
    feedback = _build_feedback(public, per_game)
    return {
        "combined_score": combined,
        "public": public,
        "private": private,
        "text_feedback": feedback,
    }


# ---------- cross-game diagnostic aggregation ----------

def _aggregate_diagnostics(per_game: list[dict]) -> dict[str, Any]:
    if not per_game:
        return {}

    def _mean_list(key: str) -> list[float]:
        vecs = [g["diagnostics"][key] for g in per_game]
        n = len(vecs[0])
        return [round(sum(v[i] for v in vecs) / len(vecs), 3) for i in range(n)]

    def _sum_list(key: str) -> list[int]:
        vecs = [g["diagnostics"][key] for g in per_game]
        n = len(vecs[0])
        return [sum(v[i] for v in vecs) for i in range(n)]

    shapes: dict[str, int] = {}
    for g in per_game:
        s = g["diagnostics"]["curve_shape"]
        shapes[s] = shapes.get(s, 0) + 1

    home_lost_turns = [g["diagnostics"]["home_lost_turn"] for g in per_game
                       if g["diagnostics"]["home_lost_turn"] is not None]
    fleet_deaths_all = {"sun": 0, "oob": 0, "combat": 0, "expired": 0}
    my_fleet_deaths_all = {"sun": 0, "oob": 0, "combat": 0, "expired": 0}
    for g in per_game:
        for k, v in g["diagnostics"]["fleet_deaths"].items():
            fleet_deaths_all[k] += v
        for k, v in g["diagnostics"]["my_fleet_deaths"].items():
            my_fleet_deaths_all[k] += v

    per_opp_mode: dict[str, list[list[float]]] = {}
    for g in per_game:
        if g["mode"] == "2p":
            key = f"2p:{g['opponent']}"
            per_opp_mode.setdefault(key, []).append(g["diagnostics"]["trajectory_share_top_binned"])
        else:
            for opp in g.get("opponents", []):
                key = f"4p:{opp}"
                per_opp_mode.setdefault(key, []).append(g["diagnostics"]["trajectory_share_top_binned"])
    mean_traj_by_opp_mode = {
        k: [round(sum(v[i] for v in vecs) / len(vecs), 3) for i in range(NUM_BINS)]
        for k, vecs in per_opp_mode.items()
    }

    place_counts = {str(place): 0 for place in range(1, 5)}
    seat_counts = {str(slot): 0 for slot in range(4)}
    for g in per_game:
        place_counts[str(g["stats"]["final_rank"])] = place_counts.get(str(g["stats"]["final_rank"]), 0) + 1
        seat = str(g.get("candidate_slot", 0))
        seat_counts[seat] = seat_counts.get(seat, 0) + 1

    worst = sorted(per_game, key=lambda g: g["composite"])[:3]
    worst_games = [
        {
            "opponent": g["opponent"],
            "opponents": g.get("opponents", []),
            "mode": g["mode"],
            "swap": g["swap"],
            "candidate_slot": g.get("candidate_slot", 0),
            "seed": g["seed"],
            "composite": round(g["composite"], 3),
            "curve_shape": g["diagnostics"]["curve_shape"],
            "home_lost_turn": g["diagnostics"]["home_lost_turn"],
            "pivotal_turn": g["diagnostics"]["pivotal_turn"],
            "pivotal_drop": g["diagnostics"]["pivotal_drop"],
            "trajectory_share_top_binned": g["diagnostics"]["trajectory_share_top_binned"],
            "trajectory_share_field_binned": g["diagnostics"]["trajectory_share_field_binned"],
            "captures_by_bin": g["diagnostics"]["captures_by_bin"],
            "losses_by_bin": g["diagnostics"]["losses_by_bin"],
            "launches_by_bin": g["diagnostics"]["launches_by_bin"],
            "launch_ships_by_bin": g["diagnostics"]["launch_ships_by_bin"],
            "rejected_launches_by_bin": g["diagnostics"]["rejected_launches_by_bin"],
            "my_fleet_deaths": g["diagnostics"]["my_fleet_deaths"],
        }
        for g in worst
    ]

    return {
        "num_bins": NUM_BINS,
        "curve_shape_counts": shapes,
        "mean_trajectory_share_top_binned": _mean_list("trajectory_share_top_binned"),
        "mean_trajectory_share_field_binned": _mean_list("trajectory_share_field_binned"),
        "mean_trajectory_prod_share_binned": _mean_list("trajectory_prod_share_binned"),
        "mean_my_planets_binned": _mean_list("my_planets_binned"),
        "mean_my_ships_binned": _mean_list("my_ships_binned"),
        "mean_my_prod_binned": _mean_list("my_prod_binned"),
        "mean_opp_top_ships_binned": _mean_list("opp_top_ships_binned"),
        "sum_captures_by_bin": _sum_list("captures_by_bin"),
        "sum_losses_by_bin": _sum_list("losses_by_bin"),
        "sum_launches_by_bin": _sum_list("launches_by_bin"),
        "sum_launch_ships_by_bin": _sum_list("launch_ships_by_bin"),
        "sum_rejected_launches_by_bin": _sum_list("rejected_launches_by_bin"),
        "mean_trajectory_by_opp_mode": mean_traj_by_opp_mode,
        "home_lost_rate": round(len(home_lost_turns) / len(per_game), 3),
        "mean_home_lost_turn": (
            round(sum(home_lost_turns) / len(home_lost_turns), 1)
            if home_lost_turns else None
        ),
        "place_counts": place_counts,
        "candidate_seat_counts": seat_counts,
        "fleet_deaths_total": fleet_deaths_all,
        "my_fleet_deaths_total": my_fleet_deaths_all,
        "worst_games": worst_games,
    }


# ---------- text feedback: compact guide for mutation prompts ----------

def _build_feedback(public: dict, per_game: list[dict]) -> str:
    """Static legend for the compact public dict.

    Keep this short: `public` is the main LLM-facing diagnostic channel.
    """
    return (
        "HOW TO READ public: combined_score is the only optimized scalar; public is "
        "diagnostic context for mutation. score_summary shows 2P/4P/all aggregate "
        "performance, rank, survival, production share, and worst mode. "
        "\n\n"
        "cluster_summary anonymizes opponents into behavior clusters, so improve the "
        "general failure mode rather than coding to bot names. "
        "\n\n"
        "failure_signals lists home-loss rate, placement, trajectory shape counts, and "
        "compact worst-game summaries with trajectory bins/captures/losses. "
        "\n\n"
        "mutation_guidance is a short natural-language hint derived from diagnostics. "
        "Do not overfit to one cluster; prefer changes that improve both 2P and mixed 4P. "
        "step_timing must stay below the 1.0s/turn hard cap."
    )


# ---------- CLI smoke test ----------

if __name__ == "__main__":
    import json

    result = evaluate(
        nearest_planet_sniper,
        EvalConfig(seeds_per_2p_opponent=1, four_player_matches=4),
    )
    print("combined_score:", result["combined_score"])
    print("text_feedback:", result["text_feedback"])
    print()
    print("public:")
    print(json.dumps(result["public"], indent=2, default=str))
