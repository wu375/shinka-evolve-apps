"""Self-play novelty evaluator for SVG/image ShinkaEvolve tasks.

The evaluator renders four stochastic candidate samples into a 2x2 gallery,
samples gallery images from a fixed opponent pool, shuffles the candidate
gallery among them, and asks Gemini to rank all galleries for each rubric
criterion. The judge never sees which gallery is the candidate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
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


JUDGE_MODEL = "gemini-3-flash-preview"
JUDGE_TIMEOUT_SECONDS = 90
FORBIDDEN_SVG_PATTERNS = (
    r"<\s*script\b",
    r"\bon\w+\s*=",
    r"\b(?:href|xlink:href)\s*=\s*['\"]\s*(?:https?:|data:|javascript:)",
    r"<\s*foreignObject\b",
)


def _load_dotenv_from_workspace(start: Path) -> None:
    for d in [start, *start.parents]:
        p = d / ".env"
        if p.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(p, override=False)
                return
            except Exception:
                pass
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def _find_rubric(pool_dir: Path, results_dir: Path) -> Path:
    """Search for rubric.md in the task workspace.

    Shinka copies evaluate.py to a temp results dir, so __file__ is unreliable.
    Search upward from pool_dir.parent (the actual task workspace) and from
    results_dir parents to handle various layouts.
    """
    search_dirs = [
        Path(__file__).resolve().parent,
        pool_dir.parent,
        *Path(results_dir).resolve().parents,
        Path(results_dir).resolve(),
    ]
    for d in search_dirs:
        candidate = d / "rubric.md"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"rubric.md not found. Searched: {[str(d) for d in search_dirs]}"
    )


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
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as f:
        f.write(svg_text)
        svg_path = Path(f.name)
    try:
        _render_svg_file_to_png(svg_path, png_path, size=size)
    finally:
        svg_path.unlink(missing_ok=True)


def _render_svg_file_to_png(svg_path: Path, png_path: Path, size: int = 1024) -> None:
    # rsvg-convert is the preferred renderer: correctly handles hsl() CSS colors.
    # cairosvg silently renders hsl() as black — do NOT use it as the primary renderer.
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
        subprocess.run(
            [magick, str(svg_path), "-resize", f"{size}x{size}", str(png_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return

    try:
        cairosvg = importlib.import_module("cairosvg")
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=size, output_height=size)
        return
    except Exception:
        pass

    raise RuntimeError(
        "No SVG renderer found. Install rsvg-convert (recommended) or ImageMagick. "
        "cairosvg silently renders hsl() colors as black and is only used as last resort."
    )


def _ensure_png(image_path: Path, work_dir: Path, label: str) -> Path:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        return image_path
    if suffix == ".svg":
        png_path = work_dir / f"{label}.png"
        _render_svg_file_to_png(image_path, png_path)
        return png_path
    raise ValueError(f"Unsupported opponent image type: {image_path}")


def _compose_gallery(sample_paths: list[Path], gallery_path: Path, tile_size: int = 1024) -> None:
    if len(sample_paths) != 4:
        raise ValueError("Gallery composition expects exactly 4 sample PNGs")
    try:
        from PIL import Image
        canvas = Image.new("RGB", (tile_size * 2, tile_size * 2), "white")
        for idx, sample_path in enumerate(sample_paths):
            with Image.open(sample_path) as image:
                tile = image.convert("RGB").resize((tile_size, tile_size))
            x = (idx % 2) * tile_size
            y = (idx // 2) * tile_size
            canvas.paste(tile, (x, y))
        canvas.save(gallery_path)
        return
    except Exception:
        pass

    magick = shutil.which("magick") or shutil.which("montage")
    if magick:
        cmd = [magick, "montage", *[str(p) for p in sample_paths],
               "-tile", "2x2", "-geometry", f"{tile_size}x{tile_size}+0+0", str(gallery_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return

    raise RuntimeError("No gallery composer found. Install Pillow or ImageMagick.")


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
    image_paths: list[Path],
    rubric_text: str,
    judge_model: str,
) -> dict[str, Any]:
    """Call Gemini judge with a timeout to prevent TCP zombie hangs (Vertex AI)."""
    def _call() -> dict[str, Any]:
        # Use ShinkaEvolve's internal LLMClient so image transport, retries,
        # and auth are handled consistently with the rest of the pipeline.
        from shinka.llm import LLMClient

        client = LLMClient(
            model=judge_model,
            temperatures=[0.0],
            max_tokens=8192,
        )
        sampled_kwargs = client.get_kwargs()
        sampled_kwargs["images"] = image_paths
        sampled_kwargs["thinking_budget"] = 0

        result = client.query(
            msg=_build_judge_prompt(rubric_text, len(image_paths)),
            system_msg="You are a visual novelty judge. Return only valid JSON.",
            llm_kwargs=sampled_kwargs,
        )
        return _read_json_response(result.content or "{}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_call)
        return future.result(timeout=JUDGE_TIMEOUT_SECONDS)


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
        p for p in sorted(opponents_dir.iterdir())
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


def _candidate_gallery(
    module: Any,
    base_seed: int,
    results_dir: Path,
    max_svg_characters: int,
    sample_count: int = 4,
) -> tuple[Path, list[dict[str, Any]]]:
    sample_records: list[dict[str, Any]] = []
    sample_pngs: list[Path] = []
    for sample_idx in range(sample_count):
        seed = int(base_seed) + sample_idx
        svg_text = _candidate_svg(module, seed)
        _validate_svg(svg_text, max_characters=max_svg_characters)
        svg_path = results_dir / f"sample_{sample_idx + 1:02d}.svg"
        png_path = results_dir / f"sample_{sample_idx + 1:02d}.png"
        svg_path.write_text(svg_text, encoding="utf-8")
        _render_svg_text_to_png(svg_text, png_path)
        sample_pngs.append(png_path)
        sample_records.append({"seed": seed, "svg_path": str(svg_path), "png_path": str(png_path)})

    gallery_path = results_dir / "gallery.png"
    _compose_gallery(sample_pngs, gallery_path)
    return gallery_path, sample_records


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
    max_svg_characters: int = 2_000_000,
) -> dict[str, Any]:
    # Brief pause so asyncio on macOS can register its SIGCHLD handler before
    # we exit — prevents process.wait() hanging on fast-failing candidates.
    import time as _time
    _time.sleep(2)

    _load_dotenv_from_workspace(Path(__file__).resolve().parent)
    _load_dotenv_from_workspace(Path(results_dir).resolve())

    rdir = Path(results_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    try:
        module = _import_candidate(program_path)
        target_seed = int(base_seed)

        pool_dir = Path(opponents_dir)
        if not pool_dir.is_absolute():
            pool_dir = Path(__file__).resolve().parent / opponents_dir
        opponents = _collect_opponents(pool_dir)
        if len(opponents) < n_opponents:
            raise ValueError(
                f"Need at least {n_opponents} opponents, found {len(opponents)} in {pool_dir}"
            )

        rubric_text = _find_rubric(pool_dir, results_dir).read_text(encoding="utf-8")

        target_gallery, target_samples = _candidate_gallery(
            module=module,
            base_seed=target_seed,
            results_dir=rdir,
            max_svg_characters=max_svg_characters,
        )

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

            items: list[dict[str, Any]] = [
                {"kind": "target", "source": str(target_gallery), "png_path": target_gallery}
            ]
            for opp_idx, opp_path in enumerate(sampled):
                png_path = _ensure_png(opp_path, work_dir, f"opponent_{opp_idx + 1:02d}")
                items.append({"kind": "opponent", "source": str(opp_path), "png_path": png_path})
            rng.shuffle(items)

            target_index = next(i for i, item in enumerate(items) if item["kind"] == "target")
            ordered_image_paths = [Path(item["png_path"]) for item in items]

            judge_result = _judge_images(ordered_image_paths, rubric_text, judge_model)
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

            game_records.append({
                "game": game_idx + 1,
                "image_paths": [str(p) for p in ordered_image_paths],
                "target_index": target_index + 1,
                "opponents": [str(p) for p in sampled],
                "criteria": criterion_records,
            })

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
                "target_gallery": str(target_gallery),
                "target_samples": target_samples,
            },
            "extra_data": {"image_path": str(target_gallery)},
            "text_feedback": text_feedback,
        }
        _write_outputs(rdir, metrics, correct=True, error="")

        # Copy gallery to top-level galleries/ dir for easy review across generations.
        try:
            combined_score_val = metrics["combined_score"]
            galleries_out = pool_dir.parent / "galleries"
            galleries_out.mkdir(parents=True, exist_ok=True)
            gen_label = rdir.parent.name
            dest = galleries_out / f"{gen_label}_score_{combined_score_val:.3f}.png"
            shutil.copy2(target_gallery, dest)
        except Exception:
            pass

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
    parser.add_argument("--max_svg_characters", type=int, default=2_000_000)
    args = parser.parse_args()
    evaluate_self_play_novelty(
        program_path=args.program_path,
        results_dir=args.results_dir,
        opponents_dir=args.opponents_dir,
        n_opponents=args.n_opponents,
        n_games=args.n_games,
        base_seed=args.base_seed,
        judge_model=args.judge_model,
        max_svg_characters=args.max_svg_characters,
    )
