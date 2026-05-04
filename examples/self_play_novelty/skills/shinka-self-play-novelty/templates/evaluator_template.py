"""Self-play novelty evaluator for SVG/image ShinkaEvolve tasks.

The evaluator renders the candidate SVG, samples opponent images from a fixed
pool, shuffles the candidate among them, and asks Gemini to rank all images for
each rubric criterion. The judge never sees which image is the candidate.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


RUBRIC_PATH = Path(__file__).parent / "rubric.md"
JUDGE_MODEL = "gemini-3-flash-preview"
FORBIDDEN_SVG_PATTERNS = (
    r"<\s*script\b",
    r"\bon\w+\s*=",
    r"\b(?:href|xlink:href)\s*=\s*['\"]\s*(?:https?:|data:|javascript:)",
    r"<\s*foreignObject\b",
)


def _load_dotenv_from_workspace(start: Path) -> None:
    candidate: Path | None = None
    for d in [start, *start.parents]:
        p = d / ".env"
        if p.exists():
            candidate = p
            break
    if candidate is None:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(candidate, override=False)
        return
    except Exception:
        pass
    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _import_candidate(program_path: str):
    spec = importlib.util.spec_from_file_location("candidate", program_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load candidate from {program_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["candidate"] = module
    spec.loader.exec_module(module)
    return module


def _validate_svg(svg_text: str, max_characters: int) -> None:
    if not isinstance(svg_text, str):
        raise ValueError("generate_svg must return a string")
    if len(svg_text) > max_characters:
        raise ValueError(f"SVG has {len(svg_text)} characters; limit is {max_characters}")
    for pattern in FORBIDDEN_SVG_PATTERNS:
        if re.search(pattern, svg_text, flags=re.I):
            raise ValueError(f"SVG contains forbidden pattern: {pattern}")

    root = ET.fromstring(svg_text)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag != "svg":
        raise ValueError("SVG root element must be <svg>")
    if not (root.get("viewBox") or (root.get("width") and root.get("height"))):
        raise ValueError("SVG must include viewBox or width/height")


def _render_svg_text_to_png(svg_text: str, png_path: Path, size: int = 1024) -> None:
    try:
        cairosvg = importlib.import_module("cairosvg")

        cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"),
            write_to=str(png_path),
            output_width=size,
            output_height=size,
        )
        return
    except Exception:
        pass

    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as f:
        f.write(svg_text)
        svg_path = Path(f.name)
    try:
        _render_svg_file_to_png(svg_path, png_path, size=size)
    finally:
        svg_path.unlink(missing_ok=True)


def _render_svg_file_to_png(svg_path: Path, png_path: Path, size: int = 1024) -> None:
    try:
        cairosvg = importlib.import_module("cairosvg")

        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=size, output_height=size)
        return
    except Exception:
        pass

    rsvg_convert = shutil.which("rsvg-convert")
    if rsvg_convert:
        subprocess.run(
            [rsvg_convert, "-w", str(size), "-h", str(size), "-o", str(png_path), str(svg_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return

    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        cmd = [magick, str(svg_path), "-resize", f"{size}x{size}", str(png_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return

    raise RuntimeError("No SVG renderer found. Install cairosvg, rsvg-convert, or ImageMagick.")


def _ensure_png(image_path: Path, work_dir: Path, label: str) -> Path:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        return image_path
    if suffix == ".svg":
        png_path = work_dir / f"{label}.png"
        _render_svg_file_to_png(image_path, png_path)
        return png_path
    raise ValueError(f"Unsupported opponent image type: {image_path}")


def _read_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _build_judge_prompt(rubric_text: str, num_images: int) -> str:
    return f"""You are ranking visual novelty candidates.

You received {num_images} images as separate inputs, in order. Refer to them as Image 1 through Image {num_images}; the first image part is Image 1, the second image part is Image 2, and so on.
One image is the hidden candidate. The rest are opponent-pool images. You are not told which is which.

Rubric:
{rubric_text}

Return only JSON. The JSON must contain a "ranks" object mapping criterion names to integer arrays.
Each array must have exactly {num_images} integers in input image order.
Rank 1 is best. Use every rank from 1 to {num_images} exactly once per criterion. Do not tie.
"""


def _judge_images(
    image_paths: list[Path], rubric_text: str, model: str
) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client()
    contents: list[Any] = [
        types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/png")
        for image_path in image_paths
    ]
    contents.append(_build_judge_prompt(rubric_text, num_images=len(image_paths)))
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    return _read_json_response(response.text or "{}")


def _validate_ranks(judge_result: dict[str, Any], num_images: int) -> dict[str, list[int]]:
    ranks = judge_result.get("ranks", judge_result)
    if not isinstance(ranks, dict):
        raise ValueError("Judge response must include a ranks object")

    expected = set(range(1, num_images + 1))
    normalized: dict[str, list[int]] = {}
    for criterion, values in ranks.items():
        if not isinstance(values, list) or len(values) != num_images:
            raise ValueError(f"Criterion {criterion!r} must have {num_images} ranks")
        ints = [int(v) for v in values]
        if set(ints) != expected:
            raise ValueError(f"Criterion {criterion!r} must use ranks 1..{num_images} exactly once")
        normalized[str(criterion)] = ints

    if not normalized:
        raise ValueError("Judge returned no rank criteria")
    return normalized


def _collect_opponents(opponents_dir: Path) -> list[Path]:
    if not opponents_dir.exists():
        raise FileNotFoundError(f"Opponent pool not found: {opponents_dir}")
    images = [
        p
        for p in sorted(opponents_dir.iterdir())
        if p.is_file() and p.suffix.lower() in {".png", ".svg"}
    ]
    if not images:
        raise ValueError(f"No PNG/SVG opponent images found in {opponents_dir}")
    return images


def _candidate_svg(module: Any, seed: int) -> str:
    if hasattr(module, "generate_svg"):
        return module.generate_svg(seed)
    if hasattr(module, "run_experiment"):
        outputs = module.run_experiment([seed])
        if isinstance(outputs, list) and outputs:
            return outputs[0]
    raise AttributeError("Candidate must expose generate_svg(rng: int) -> str")


def _write_outputs(results_dir: Path, metrics: dict[str, Any], correct: bool, error: str) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (results_dir / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2), encoding="utf-8"
    )


def _failure(results_dir: Path, error: str) -> dict[str, Any]:
    metrics = {
        "combined_score": 0.0,
        "public": {"error": error[:500]},
        "private": {},
        "text_feedback": f"Evaluation failed: {error[:1000]}",
    }
    _write_outputs(results_dir, metrics, correct=False, error=error)
    return metrics


def evaluate_self_play_novelty(
    program_path: str,
    results_dir: str,
    opponents_dir: str = "opponents",
    n_opponents: int = 3,
    n_games: int = 2,
    base_seed: int = 42,
    judge_model: str = JUDGE_MODEL,
    max_svg_characters: int = 50000,
) -> dict[str, Any]:
    _load_dotenv_from_workspace(Path(__file__).resolve().parent)
    rdir = Path(results_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    try:
        module = _import_candidate(program_path)
        target_seed = int(base_seed)
        svg_text = _candidate_svg(module, target_seed)
        _validate_svg(svg_text, max_svg_characters)

        target_svg = rdir / "target.svg"
        target_png = rdir / "target.png"
        target_svg.write_text(svg_text, encoding="utf-8")
        _render_svg_text_to_png(svg_text, target_png)

        pool_dir = Path(opponents_dir)
        if not pool_dir.is_absolute():
            pool_dir = Path(__file__).resolve().parent / pool_dir
        opponents = _collect_opponents(pool_dir)
        if len(opponents) < n_opponents:
            raise ValueError(
                f"Need at least {n_opponents} opponents, found {len(opponents)} in {pool_dir}"
            )

        rubric_text = RUBRIC_PATH.read_text(encoding="utf-8")
        per_criterion_scores: dict[str, list[float]] = {}
        target_ranks: list[int] = []
        first_places = 0
        criterion_count = 0
        game_records: list[dict[str, Any]] = []

        for game_idx in range(int(n_games)):
            rng = random.Random(int(base_seed) + game_idx * 9973)
            sampled = rng.sample(opponents, int(n_opponents))
            work_dir = rdir / f"game_{game_idx + 1:02d}"
            work_dir.mkdir(parents=True, exist_ok=True)

            items: list[dict[str, Any]] = [{"kind": "target", "source": str(target_png), "png_path": target_png}]
            for opp_idx, opp_path in enumerate(sampled):
                png_path = _ensure_png(opp_path, work_dir, f"opponent_{opp_idx + 1:02d}")
                items.append({"kind": "opponent", "source": str(opp_path), "png_path": png_path})
            rng.shuffle(items)

            target_index = next(i for i, item in enumerate(items) if item["kind"] == "target")
            ordered_image_paths = [Path(item["png_path"]) for item in items]

            judge_result = _judge_images(
                image_paths=ordered_image_paths,
                rubric_text=rubric_text,
                model=judge_model,
            )
            ranks = _validate_ranks(judge_result, len(items))

            criterion_records = {}
            for criterion, values in ranks.items():
                target_rank = values[target_index]
                score = (len(items) - target_rank) / (len(items) - 1)
                per_criterion_scores.setdefault(criterion, []).append(float(score))
                target_ranks.append(int(target_rank))
                if target_rank == 1:
                    first_places += 1
                criterion_count += 1
                criterion_records[criterion] = {
                    "target_rank": int(target_rank),
                    "normalized_score": float(score),
                }

            game_records.append(
                {
                    "game": game_idx + 1,
                    "image_paths": [str(p) for p in ordered_image_paths],
                    "target_index": target_index + 1,
                    "opponents": [str(p) for p in sampled],
                    "criteria": criterion_records,
                }
            )

        public = {
            criterion: sum(values) / len(values)
            for criterion, values in sorted(per_criterion_scores.items())
        }
        all_scores = [score for values in per_criterion_scores.values() for score in values]
        combined_score = float(sum(all_scores) / len(all_scores)) if all_scores else 0.0
        public["avg_target_rank"] = float(sum(target_ranks) / len(target_ranks)) if target_ranks else 0.0
        public["first_place_rate"] = float(first_places / criterion_count) if criterion_count else 0.0

        weakest = sorted(public.items(), key=lambda kv: kv[1])[:2]
        text_feedback = (
            f"Self-play novelty score {combined_score:.3f}. "
            f"Weakest public criteria: {', '.join(f'{k}={v:.3f}' for k, v in weakest)}. "
            "Try changes that improve the weakest ranked dimensions while preserving SVG validity."
        ).strip()

        metrics = {
            "combined_score": combined_score,
            "public": public,
            "private": {
                "games": game_records,
                "pool_size": len(opponents),
                "target_seed": target_seed,
                "target_png": str(target_png),
            },
            "extra_data": {"image_path": str(target_png)},
            "text_feedback": text_feedback,
        }
        _write_outputs(rdir, metrics, correct=True, error="")
        return metrics
    except Exception:
        return _failure(rdir, traceback.format_exc())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    parser.add_argument("--opponents_dir", default="opponents")
    parser.add_argument("--n_opponents", type=int, default=3)
    parser.add_argument("--n_games", type=int, default=2)
    parser.add_argument("--base_seed", type=int, default=42)
    parser.add_argument("--judge_model", default=JUDGE_MODEL)
    args = parser.parse_args()
    evaluate_self_play_novelty(
        program_path=args.program_path,
        results_dir=args.results_dir,
        opponents_dir=args.opponents_dir,
        n_opponents=args.n_opponents,
        n_games=args.n_games,
        base_seed=args.base_seed,
        judge_model=args.judge_model,
    )
