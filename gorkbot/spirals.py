"""Arity's reusable terminal brand mark, generated with Vogel phyllotaxis."""
from __future__ import annotations

import math


def render_brand_mark(
    *,
    width: int = 31,
    height: int = 13,
    seeds: int = 89,
    tagline: bool = True,
) -> str:
    """Render a compact ASCII sunflower and, optionally, Arity's CLI tagline."""
    if width < 9 or height < 5 or seeds < 1:
        raise ValueError("brand mark requires width >= 9, height >= 5, and seeds >= 1")

    canvas = [[" " for _ in range(width)] for _ in range(height)]
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    max_radius = math.sqrt(seeds)
    scale = min(center_x / 2.0, center_y) * 0.9 / max_radius
    palette = ".o*"

    for index in range(1, seeds + 1):
        radius = math.sqrt(index) * scale
        theta = index * golden_angle
        x = round(center_x + radius * math.cos(theta) * 2.0)
        y = round(center_y + radius * math.sin(theta))
        if 0 <= x < width and 0 <= y < height:
            shade = min(len(palette) - 1, index * len(palette) // (seeds + 1))
            canvas[y][x] = palette[shade]

    canvas[round(center_y)][round(center_x)] = "@"
    mark = "\n".join("".join(row).rstrip() for row in canvas).rstrip()
    if tagline:
        mark += "\nArity | one task, N agents, facts first"
    return mark
