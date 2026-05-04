#!/usr/bin/env python3
"""Round helper for self-play novelty image pools.

This script keeps the opponent pool fixed during each ShinkaEvolve run, then
promotes top rendered candidate images into the pool for the next round.
Adjust the `shinka run` command if your installed CLI uses different flags.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def snapshot_pool(workspace: Path, round_dir: Path) -> Path:
    opponents = workspace / "opponents"
    if not opponents.exists():
        raise FileNotFoundError(f"Missing opponent pool: {opponents}")
    snapshot = round_dir / "pool_snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(opponents, snapshot)
    return snapshot


def run_shinka(workspace: Path, config: Path, generations: int) -> None:
    cmd = ["shinka", "run", "--config", str(config), "--num-generations", str(generations)]
    subprocess.run(cmd, cwd=workspace, check=True)


def score_from_metrics(path: Path) -> float:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("combined_score", 0.0))
    except Exception:
        return 0.0


def image_from_metrics(metrics_path: Path) -> Path | None:
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    candidates = [
        data.get("extra_data", {}).get("image_path"),
        data.get("private", {}).get("target_png"),
    ]
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = path if path.exists() else metrics_path.parent / path
        if path.exists() and path.suffix.lower() == ".png":
            return path

    fallback = metrics_path.parent / "target.png"
    if fallback.exists():
        return fallback
    return None


def collect_top_images(results_root: Path, top_k: int) -> list[tuple[float, Path]]:
    scored: list[tuple[float, Path]] = []
    for metrics_path in results_root.glob("**/metrics.json"):
        image = image_from_metrics(metrics_path)
        if image is None:
            continue
        scored.append((score_from_metrics(metrics_path), image))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def promote_images(
    workspace: Path,
    round_idx: int,
    top_images: list[tuple[float, Path]],
    max_promoted: int,
) -> list[Path]:
    opponents = workspace / "opponents"
    opponents.mkdir(parents=True, exist_ok=True)
    promoted: list[Path] = []
    for rank, (score, image) in enumerate(top_images, start=1):
        dest = opponents / f"round_{round_idx:03d}_rank_{rank:02d}_score_{score:.3f}.png"
        shutil.copy2(image, dest)
        promoted.append(dest)

    non_baselines = sorted(
        [p for p in opponents.glob("*.png") if not p.name.startswith("baseline_")],
        key=lambda p: p.stat().st_mtime,
    )
    while len(non_baselines) > max_promoted:
        old = non_baselines.pop(0)
        old.unlink(missing_ok=True)
    return promoted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("shinka.yaml"))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--generations-per-round", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--rounds-kept", type=int, default=3)
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    config = args.config if args.config.is_absolute() else workspace / args.config
    results_root = args.results_root if args.results_root.is_absolute() else workspace / args.results_root
    max_promoted = args.top_k * args.rounds_kept

    for round_idx in range(1, args.rounds + 1):
        round_dir = workspace / "rounds" / f"round_{round_idx:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_pool(workspace, round_dir)
        print(f"[round {round_idx}] snapshotted pool to {snapshot}")

        run_shinka(workspace, config, args.generations_per_round)

        top_images = collect_top_images(results_root, args.top_k)
        promoted = promote_images(workspace, round_idx, top_images, max_promoted)
        (round_dir / "promoted.json").write_text(
            json.dumps([str(p) for p in promoted], indent=2),
            encoding="utf-8",
        )
        print(f"[round {round_idx}] promoted {len(promoted)} images")


if __name__ == "__main__":
    main()
