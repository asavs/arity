"""arity spirals — Procedural mathematical ASCII spiral engines and micro-spinners.

Generates real-time procedural phyllotaxis, logarithmic spirals, and micro-spinners
for terminal UIs, startup animations, and inference thinking states.
"""
from __future__ import annotations

import math
from typing import Iterator


class ProceduralSpiralRenderer:
    """Procedurally renders mathematical curves on a 2D text canvas."""

    @staticmethod
    def render_vogel_sunflower(
        num_seeds: int = 150,
        width: int = 55,
        height: int = 25,
        aspect_ratio: float = 2.1,  # Terminal character height/width ratio
        progress: float = 1.0,      # 0.0 to 1.0 for dynamic blooming animation
        char_palette: str = " ·:+*#@",
    ) -> str:
        """Procedurally render Vogel's golden phyllotaxis spiral up to progress percentage."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        golden_angle = math.pi * (3.0 - math.sqrt(5.0))  # ~137.507764 degrees in radians

        max_n = max(1, int(num_seeds * max(0.01, min(1.0, progress))))
        max_r = math.sqrt(num_seeds)
        scale = min(cx / aspect_ratio, cy) * 0.9 / max_r

        for n in range(1, max_n + 1):
            r = math.sqrt(n) * scale
            theta = n * golden_angle

            x = int(cx + r * math.cos(theta) * aspect_ratio)
            y = int(cy + r * math.sin(theta))

            if 0 <= x < width and 0 <= y < height:
                # Intensity based on distance from center (core is dense, outer is light)
                palette_idx = min(len(char_palette) - 1, int((n / num_seeds) * (len(char_palette) - 1)))
                canvas[y][x] = char_palette[len(char_palette) - 1 - palette_idx]

        # Draw center seed
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
        """Procedurally render Spira Mirabilis (r = a * exp(b * theta))."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        b = 0.18
        max_theta = turns * 2.0 * math.pi * max(0.05, min(1.0, progress))
        steps = 400

        for i in range(steps):
            theta = (i / steps) * max_theta
            r = math.exp(b * theta)
            max_r = math.exp(b * (turns * 2.0 * math.pi))
            scale = min(cx / aspect_ratio, cy) * 0.85 / max_r

            x = int(cx + r * scale * math.cos(theta) * aspect_ratio)
            y = int(cy + r * scale * math.sin(theta))

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
        """Procedurally render Rhodonea Rose Curve (r = a * cos(k * theta))."""
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        cx, cy = width / 2.0, height / 2.0
        max_theta = math.pi * (1 if petals % 2 != 0 else 2) * max(0.05, min(1.0, progress))
        steps = 500
        scale = min(cx / aspect_ratio, cy) * 0.88

        for i in range(steps):
            theta = (i / steps) * max_theta
            r = scale * math.cos(petals * theta)

            x = int(cx + r * math.cos(theta) * aspect_ratio)
            y = int(cy + r * math.sin(theta))

            if 0 <= x < width and 0 <= y < height:
                canvas[y][x] = "*" if i % 3 == 0 else "·"

        canvas[int(cy)][int(cx)] = "✿"
        return "\n".join("".join(row) for row in canvas)


# -----------------------------------------------------------------------------
# Micro-Spinners for Inference & Thinking States
# -----------------------------------------------------------------------------

class SpiralSpinners:
    """Compact animated thinking spinners based on polar geometry and arity."""

    # 1-cell Golden Ratio Braille Vortex
    BRAILLE_VORTEX = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    # 1-cell Polar Orbit Spinners
    POLAR_ORBITS = ("◜", "◝", "◞", "◟")
    SUNFLOWER_PULSE = ("◌", "◷", "◶", "◵", "◴", "●")

    # 3-line Thinking Blossom (Breathing in and out during model reasoning)
    BLOSSOM_FRAMES = (
        # Frame 1: Contracted Seed
        "   ·   \n"
        " ·(•)· \n"
        "   ·   ",
        # Frame 2: Opening Petals
        "  . ' .  \n"
        " '( • )' \n"
        "  ' . '  ",
        # Frame 3: Full Golden Bloom
        " : * : \n"
        "* (•) *\n"
        " : * : ",
        # Frame 4: Radiating Corona
        " . ' * ' . \n"
        "' * (•) * '\n"
        " . ' * ' . ",
    )

    @classmethod
    def get_arity_spinner(cls, arity: int, frame_idx: int) -> str:
        """Return a dynamic micro-spinner indicating how many models/kernels are thinking."""
        if arity <= 1:
            # Unary: Single central orbiting spiral
            symbol = cls.BRAILLE_VORTEX[frame_idx % len(cls.BRAILLE_VORTEX)]
            return f"\033[1;33m{symbol}\033[0m [arity:1 thinking]"
        elif arity == 2:
            # Binary: Double Lariat twin rotating vortex
            s1 = cls.POLAR_ORBITS[frame_idx % len(cls.POLAR_ORBITS)]
            s2 = cls.POLAR_ORBITS[(frame_idx + 2) % len(cls.POLAR_ORBITS)]
            return f"\033[1;36m{s1}\033[0m⚡\033[1;35m{s2}\033[0m [arity:2 tag-team trial]"
        else:
            # Multipolar N-ary: Multipolar Sunflower Terrarium
            syms = [cls.BRAILLE_VORTEX[(frame_idx + i * 3) % len(cls.BRAILLE_VORTEX)] for i in range(min(arity, 4))]
            joined = "".join(syms)
            return f"\033[1;32m{joined}\033[0m [arity:{arity} terrarium swarm]"
