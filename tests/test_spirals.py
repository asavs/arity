"""Tests for procedural spiral renderers, arity spinners, and brand visuals."""
from __future__ import annotations

import unittest

from arity.spirals import ProceduralSpiralRenderer, SpiralSpinners


class TestSpirals(unittest.TestCase):
    def test_vogel_sunflower_rendering(self):
        output = ProceduralSpiralRenderer.render_vogel_sunflower(num_seeds=50, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("●", output)
        self.assertEqual(len(output.splitlines()), 15)

    def test_logarithmic_nautilus_rendering(self):
        output = ProceduralSpiralRenderer.render_logarithmic_nautilus(turns=2.0, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("●", output)

    def test_rose_flower_rendering(self):
        output = ProceduralSpiralRenderer.render_rose_flower(petals=5, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("✿", output)

    def test_arity_spinners(self):
        unary = SpiralSpinners.get_arity_spinner(arity=1, frame_idx=0)
        self.assertIn("arity:1", unary)

        binary = SpiralSpinners.get_arity_spinner(arity=2, frame_idx=0)
        self.assertIn("arity:2", binary)
        self.assertIn("tag-team", binary)

        multipolar = SpiralSpinners.get_arity_spinner(arity=4, frame_idx=0)
        self.assertIn("arity:4", multipolar)
        self.assertIn("terrarium", multipolar)


if __name__ == "__main__":
    unittest.main()
