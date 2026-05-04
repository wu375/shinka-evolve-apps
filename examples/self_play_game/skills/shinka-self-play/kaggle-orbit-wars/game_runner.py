"""Kaggle Environments adapter for Orbit War.

Contract:
    run_match(agent_a, agent_b, seed: int) -> {
        "candidate_score": float,  # higher = better for agent_a
        "placement": int,           # 1 if agent_a wins/ties, 2 otherwise
    }
"""

from __future__ import annotations

import random
from typing import Any, Callable

ENV_NAME = "orbit_wars"


def run_match(agent_a: Callable, agent_b: Callable, seed: int) -> dict[str, Any]:
    from kaggle_environments import make

    random.seed(seed)
    env = make(ENV_NAME, configuration={"seed": seed, "agents": 2}, debug=False)
    env.run([agent_a, agent_b])
    rewards = [s.reward if s.reward is not None else 0.0 for s in env.state]
    margin = float(rewards[0] - rewards[1])
    placement = 1 if rewards[0] >= rewards[1] else 2
    return {"candidate_score": margin, "placement": placement}
