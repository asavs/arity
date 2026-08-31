"""Procedural terminal spirals, arity spinners, and Arity's brand mark."""
from __future__ import annotations

import math


class ProceduralSpiralRenderer:
    """Procedurally render mathematical curves on a two-dimensional text canvas."""

    @staticmethod
    def render_vogel_sunflower(
        num_seeds: int = 150,
        width: int = 55,
        height: int = 25,
        aspect_ratio: float = 2.1,
        progress: float = 1.0,
        char_palette: str = " ·:+*#@",
    ) -> str:
        """Render Vogel's golden-angle phyllotaxis spiral up to ``progress``."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        golden_angle = math.pi * (3.0 - math.sqrt(5.0))

        max_n = max(1, int(num_seeds * max(0.01, min(1.0, progress))))
        max_r = math.sqrt(num_seeds)
        scale = min(cx / aspect_ratio, cy) * 0.9 / max_r

        for n in range(1, max_n + 1):
            radius = math.sqrt(n) * scale
            theta = n * golden_angle
            x = int(cx + radius * math.cos(theta) * aspect_ratio)
            y = int(cy + radius * math.sin(theta))
            if 0 <= x < width and 0 <= y < height:
                palette_idx = min(len(char_palette) - 1, int((n / num_seeds) * (len(char_palette) - 1)))
                canvas[y][x] = char_palette[len(char_palette) - 1 - palette_idx]

        canvas[int(cy)][int(cx)] = "●"
        return "\n".join("".join(row) for row in canvas)

    @staticmethod
    def render_logarithmic_nautilus(
        turns: float = 3.5,
        width: int = 55,
        height: int = 25,
        aspect_ratio: float = 2.1,
        progress: float = 1.0,
    ) -> str:
        """Render the logarithmic spiral ``r = a * exp(b * theta)``."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        growth = 0.18
        max_theta = turns * 2.0 * math.pi * max(0.05, min(1.0, progress))
        steps = 400

        for i in range(steps):
            theta = (i / steps) * max_theta
            radius = math.exp(growth * theta)
            max_radius = math.exp(growth * (turns * 2.0 * math.pi))
            scale = min(cx / aspect_ratio, cy) * 0.85 / max_radius
            x = int(cx + radius * scale * math.cos(theta) * aspect_ratio)
            y = int(cy + radius * scale * math.sin(theta))
            if 0 <= x < width and 0 <= y < height:
                canvas[y][x] = "█" if i == steps - 1 else "o" if i % 4 == 0 else "·"

        canvas[int(cy)][int(cx)] = "●"
        return "\n".join("".join(row) for row in canvas)

    @staticmethod
    def render_rose_flower(
        petals: int = 5,
        width: int = 55,
        height: int = 25,
        aspect_ratio: float = 2.1,
        progress: float = 1.0,
    ) -> str:
        """Render the rhodonea curve ``r = a * cos(k * theta)``."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        max_theta = math.pi * (1 if petals % 2 != 0 else 2) * max(0.05, min(1.0, progress))
        steps = 500
        scale = min(cx / aspect_ratio, cy) * 0.88

        for i in range(steps):
            theta = (i / steps) * max_theta
            radius = scale * math.cos(petals * theta)
            x = int(cx + radius * math.cos(theta) * aspect_ratio)
            y = int(cy + radius * math.sin(theta))
            if 0 <= x < width and 0 <= y < height:
                canvas[y][x] = "*" if i % 3 == 0 else "·"

        canvas[int(cy)][int(cx)] = "✿"
        return "\n".join("".join(row) for row in canvas)


class SpiralSpinners:
    """Compact animated thinking spinners based on polar geometry and arity."""

    BRAILLE_VORTEX = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    POLAR_ORBITS = ("◜", "◝", "◞", "◟")
    SUNFLOWER_PULSE = ("◌", "◷", "◶", "◵", "◴", "●")
    BLOSSOM_FRAMES = (
        "   ·   \n ·(•)· \n   ·   ",
        "  . ' .  \n '( • )' \n  ' . '  ",
        " : * : \n* (•) *\n : * : ",
        " . ' * ' . \n' * (•) * '\n . ' * ' . ",
    )

    @classmethod
    def get_arity_spinner(cls, arity: int, frame_idx: int) -> str:
        """Return a micro-spinner indicating how many candidate kernels are active."""
        if arity <= 1:
            symbol = cls.BRAILLE_VORTEX[frame_idx % len(cls.BRAILLE_VORTEX)]
            return f"\033[1;33m{symbol}\033[0m [arity:1 thinking]"
        if arity == 2:
            first = cls.POLAR_ORBITS[frame_idx % len(cls.POLAR_ORBITS)]
            second = cls.POLAR_ORBITS[(frame_idx + 2) % len(cls.POLAR_ORBITS)]
            return f"\033[1;36m{first}\033[0m⚡\033[1;35m{second}\033[0m [arity:2 tag-team trial]"
        symbols = [
            cls.BRAILLE_VORTEX[(frame_idx + i * 3) % len(cls.BRAILLE_VORTEX)]
            for i in range(min(arity, 4))
        ]
        return f"\033[1;32m{''.join(symbols)}\033[0m [arity:{arity} terrarium swarm]"


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
