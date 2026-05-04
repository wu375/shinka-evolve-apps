"""Seed candidate for shinka-create-figure tasks.

The candidate exposes a single public function:

    make_figure(output_path: str) -> None

The evolve region is intentionally tight. Helpers, constants, and the public
contract live outside the EVOLVE block so proposal LLMs only mutate the actual
figure-construction logic.

Both SVG helpers and matplotlib are available and can be combined in a single
figure to produce rich visuals — e.g. matplotlib for charts/plots plus SVG
overlays for annotations, diagrams, or custom vector elements. The final
output is always PNG.

Keep this seed generic:
Do NOT customize this seed with task-specific shapes, labels, data, or domain
knowledge. The figure description and research context belong in `context.md`
and `rubric.md`, where both the evaluator judge and the proposal LLMs can read
them. The seed is intentionally blank/neutral so that evolution discovers what
to draw from a fair starting point, not from the agent's prior knowledge of the
task.
"""

from __future__ import annotations

import io
from html import escape
from pathlib import Path

# ---- Fixed configuration (do NOT put inside evolve block) ----
WIDTH = 1200
HEIGHT = 800


# ---- SVG helpers (fixed, outside evolve block) ----
def _svg_text(x, y, text, size=24, anchor="middle"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" '
        f'text-anchor="{anchor}" font-family="Helvetica, Arial, sans-serif">'
        f"{escape(text)}</text>"
    )


def _svg_box(x, y, w, h, label):
    return (
        f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
        f'fill="#f8f8f8" stroke="#222" stroke-width="2"/>'
        f"{_svg_text(x + w/2, y + h/2 + 8, label)}</g>"
    )


def _svg_arrow(x1, y1, x2, y2):
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="#222" stroke-width="3" marker-end="url(#arrowhead)"/>'
    )


def _svg_wrap(elements: list[str]) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg"
        width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7"
                refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#222"/>
        </marker>
      </defs>
      <rect width="100%" height="100%" fill="white"/>
      {''.join(elements)}
    </svg>"""


def _rasterize_svg_to_png(svg_text: str, output_path: str) -> None:
    """Convert SVG markup to PNG. Requires cairosvg."""
    import cairosvg  # local import so non-SVG tasks don't need this dep.

    cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        write_to=output_path,
        output_width=WIDTH,
        output_height=HEIGHT,
    )


def build_svg() -> str:
    # EVOLVE-BLOCK-START
    elements: list[str] = []
    elements.append(_svg_box(80, 340, 260, 100, "Input"))
    elements.append(_svg_box(470, 340, 260, 100, "Model"))
    elements.append(_svg_box(860, 340, 260, 100, "Output"))
    elements.append(_svg_arrow(340, 390, 470, 390))
    elements.append(_svg_arrow(730, 390, 860, 390))
    elements.append(_svg_text(WIDTH / 2, 120, "Pipeline overview", size=36))
    # EVOLVE-BLOCK-END
    return _svg_wrap(elements)


def build_matplotlib_figure(output_path: str) -> None:
    """Optional matplotlib path. Replace `build_svg` with this if your task is
    plot-like rather than diagrammatic. Keep helpers outside the evolve block."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    # EVOLVE-BLOCK-MPL-START
    fig, ax = plt.subplots(figsize=(12, 8), dpi=120)
    x = np.linspace(0, 10, 200)
    ax.plot(x, np.sin(x), label="signal A")
    ax.plot(x, np.cos(x), label="signal B")
    ax.set_xlabel("time")
    ax.set_ylabel("response")
    ax.set_title("Conceptual prototype (placeholder data)")
    ax.legend()
    ax.grid(alpha=0.3)
    # EVOLVE-BLOCK-MPL-END

    fig.tight_layout()
    fig.savefig(output_path, format="png")
    plt.close(fig)


def make_figure(output_path: str) -> None:
    """Write a renderable PNG figure to ``output_path``."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Both paths can be combined. For example, build a matplotlib plot and
    # overlay SVG annotations, or build a pure SVG diagram, or use matplotlib
    # alone. Customize for your task.
    #
    # Default: SVG path. To use matplotlib instead or in addition, call
    # build_matplotlib_figure(output_path) or combine both.
    svg_text = build_svg()
    _rasterize_svg_to_png(svg_text, str(out))


# Optional: stable wrapper for `run_shinka_eval` style evaluators.
def run_experiment(random_seed: int | None = None, output_path: str = "figure.png", **kwargs):
    make_figure(output_path)
    return 0.0, ""  # actual scoring happens in evaluate.py
