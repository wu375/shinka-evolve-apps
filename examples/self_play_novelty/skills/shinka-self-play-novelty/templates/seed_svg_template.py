from typing import List

# EVOLVE-BLOCK-START
def generate_svg(rng: int) -> str:
    # Keep imports inside the evolved function so candidates are self-contained.
    import random

    gen = random.Random(rng)
    hue = gen.randint(0, 360)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
  <rect width="800" height="800" fill="hsl({hue}, 70%, 10%)"/>
  <circle cx="400" cy="400" r="220" fill="hsl({(hue + 120) % 360}, 80%, 55%)" opacity="0.75"/>
  <circle cx="400" cy="400" r="110" fill="hsl({(hue + 240) % 360}, 80%, 65%)" opacity="0.85"/>
</svg>"""
# EVOLVE-BLOCK-END


def run_experiment(random_inputs: List[int]) -> List[str]:
    svg_outputs = [generate_svg(rng) for rng in random_inputs]
    for output in svg_outputs:
        print("Generated SVG artifact:")
        print(output)
    return svg_outputs
