"""Template evaluator for shinka-create-figure tasks.

This evaluator:

1. imports the candidate from ``program_path``,
2. calls ``make_figure(output_path)`` writing a PNG,
3. validates the rendered image,
4. asks a multimodal judge (Gemini) to score the image against rubric/context,
5. writes Shinka-compatible ``metrics.json`` and ``correct.json``.

Judge client preference order:

1. Prefer ShinkaEvolve's native LLM client (``shinka.llm``) when it supports
   both image input and structured output for the chosen Gemini model. This
   keeps cost / token tracking consistent with the rest of the run.
2. Fall back to a direct ``google-genai`` call when the native client does
   not yet support these features. Today this is the default — the multimodal
   Gemini PR and the structured output PR are not yet merged.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


# ---- Customize these for your task ------------------------------------------
RUBRIC_PATH = Path(__file__).parent / "rubric.md"
CONTEXT_PATH = Path(__file__).parent / "context.md"
JUDGE_MODEL = "gemini-3-flash-preview"
# -----------------------------------------------------------------------------


def _load_dotenv_from_workspace(start: Path) -> None:
    """Load a .env file from the workspace or any parent directory.

    Uses python-dotenv if available; otherwise falls back to a minimal parser
    so the evaluator stays runnable without the optional dependency. The judge
    needs GEMINI_API_KEY to be present in the process environment.
    """
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
    except Exception:  # noqa: BLE001
        pass
    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _import_candidate(program_path: str):
    spec = importlib.util.spec_from_file_location("candidate", program_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load candidate from {program_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["candidate"] = module
    spec.loader.exec_module(module)
    return module


def _validate_png(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"output not found at {path}"
    if path.stat().st_size == 0:
        return False, "output file is empty"
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            extrema = im.convert("L").getextrema()
            if extrema[0] == extrema[1]:
                return False, "rendered image is uniform / blank"
    except Exception as e:  # noqa: BLE001
        return False, f"PNG validation failed: {e}"
    return True, ""


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "number"},
        "visual_plausibility": {"type": "number"},
        "context_alignment": {"type": "number"},
        "clarity": {"type": "number"},
        "rendering_quality": {"type": "number"},
        "rationale": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "overall_score",
        "visual_plausibility",
        "context_alignment",
        "clarity",
        "rendering_quality",
        "rationale",
    ],
}


def _build_judge_prompt(rubric_text: str, context_text: str) -> str:
    return (
        "You are judging a research figure rendered as a PNG.\n\n"
        "## Research context\n"
        f"{context_text}\n\n"
        "## Rubric\n"
        f"{rubric_text}\n\n"
        "Return a JSON object matching the schema. Judge ONLY the rendered "
        "image; do not assume data is real unless the context says so."
    )


def _judge_via_shinka_client(
    image_path: Path, rubric_text: str, context_text: str, model: str
) -> dict[str, Any] | None:
    """Try the native ShinkaEvolve LLM client.

    Returns a parsed dict if the native client supports BOTH image input and
    structured output for the requested model; otherwise returns ``None`` so
    the caller can fall back to a direct google-genai call.

    Detection is duck-typed: we look for an image-bearing message variant and
    a structured-output / response-schema option on the client. The exact API
    depends on which PRs have landed; adjust the imports below once the
    multimodal Gemini PR and the structured output PR merge.
    """
    try:
        # The native client surface is expected to expose something like
        # ``LLMClient(model=..., output_model=...)`` plus an image content
        # type. Until those PRs land, this import path is intentionally a
        # best-effort attempt that may fail; we treat any failure as
        # "feature not yet available" and return None.
        from shinka.llm import LLMClient  # type: ignore[attr-defined]
        from shinka.llm.content import ImagePart  # type: ignore[attr-defined]
    except Exception:
        return None

    try:
        client = LLMClient(model=model, output_schema=JUDGE_SCHEMA)
        prompt = _build_judge_prompt(rubric_text, context_text)
        image_bytes = image_path.read_bytes()
        response = client.query(
            contents=[
                ImagePart(data=image_bytes, mime_type="image/png"),
                prompt,
            ],
        )
    except Exception:
        # Feature not available yet, or client signature differs — fall back.
        return None

    if isinstance(response, dict):
        return response
    if isinstance(response, str):
        try:
            return json.loads(response)
        except Exception:
            return None
    return None


def _judge_via_direct_gemini(
    image_path: Path, rubric_text: str, context_text: str, model: str
) -> dict[str, Any]:
    """Fallback: call google-genai directly."""
    from google import genai
    from google.genai import types

    image_bytes = image_path.read_bytes()
    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            _build_judge_prompt(rubric_text, context_text),
        ],
        config={
            "response_mime_type": "application/json",
            "response_json_schema": JUDGE_SCHEMA,
        },
    )
    return json.loads(response.text)


def judge_image(
    image_path: Path,
    rubric_text: str,
    context_text: str,
    model: str = JUDGE_MODEL,
) -> dict[str, Any]:
    """Multimodal judge call. Returns parsed JSON dict.

    Tries the native ShinkaEvolve LLM client first (for consistent cost /
    token tracking) and falls back to a direct google-genai call if the
    native client does not yet support image input + structured output.
    """
    result = _judge_via_shinka_client(image_path, rubric_text, context_text, model)
    if result is not None:
        return result
    return _judge_via_direct_gemini(image_path, rubric_text, context_text, model)


def _write_outputs(results_dir: Path, metrics: dict, correct: bool, error: str) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (results_dir / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2), encoding="utf-8"
    )


def _failure(results_dir: Path, error: str, partial_extra: dict | None = None) -> None:
    metrics = {
        "combined_score": -1.0,
        "public": {"error": error[:500]},
        "private": {},
        "extra_data": partial_extra or {},
        "text_feedback": f"Evaluation failed: {error[:1000]}",
    }
    _write_outputs(results_dir, metrics, correct=False, error=error)


def main(program_path: str, results_dir: str) -> None:
    _load_dotenv_from_workspace(Path(__file__).resolve().parent)
    rdir = Path(results_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    output_png = rdir / "candidate.png"

    # Step 1: run candidate
    try:
        module = _import_candidate(program_path)
        module.make_figure(str(output_png))
    except Exception:  # noqa: BLE001
        _failure(rdir, traceback.format_exc())
        return

    # Step 2: validate output
    ok, msg = _validate_png(output_png)
    if not ok:
        _failure(rdir, msg, {"image_path": str(output_png)})
        return

    # Step 3: judge
    try:
        rubric_text = _read(RUBRIC_PATH)
        context_text = _read(CONTEXT_PATH)
        judge_result = judge_image(output_png, rubric_text, context_text)
    except Exception:  # noqa: BLE001
        _failure(rdir, traceback.format_exc(), {"image_path": str(output_png)})
        return

    # Step 4: assemble Shinka metrics
    overall = float(judge_result.get("overall_score", 0.0))
    metrics = {
        "combined_score": overall,
        "public": {
            "overall_score": overall,
            "visual_plausibility": judge_result.get("visual_plausibility"),
            "context_alignment": judge_result.get("context_alignment"),
            "clarity": judge_result.get("clarity"),
            "rendering_quality": judge_result.get("rendering_quality"),
        },
        "private": {},
        "extra_data": {
            "image_path": str(output_png),
            "issues": judge_result.get("issues", []),
        },
        "text_feedback": str(judge_result.get("rationale", "")),
    }
    _write_outputs(rdir, metrics, correct=True, error="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(program_path=args.program_path, results_dir=args.results_dir)
