from __future__ import annotations


def generate_svg(rng: int) -> str:
    """Return a complete, self-contained SVG artwork."""
    # Keep imports inside the evolved function so candidates are self-contained.
    import random

    gen = random.Random(rng)
    hue = gen.randint(0, 360)

    # EVOLVE-BLOCK-START
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
  <rect width="800" height="800" fill="hsl({hue}, 70%, 10%)"/>
  <circle cx="400" cy="400" r="230" fill="hsl({(hue + 120) % 360}, 80%, 55%)" opacity="0.72"/>
  <circle cx="400" cy="400" r="105" fill="hsl({(hue + 240) % 360}, 80%, 65%)" opacity="0.86"/>
  <path d="M180 520 C280 330 520 330 620 520" fill="none" stroke="white" stroke-width="18" opacity="0.45"/>
</svg>"""
    # EVOLVE-BLOCK-END


def run_experiment(random_inputs: list[int]) -> list[str]:
    """Compatibility wrapper for Shinka/evaluator styles that expect batches."""
    return [generate_svg(rng) for rng in random_inputs]
