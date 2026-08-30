"""Tests for procedural spiral mathematical renderers and arity spinners."""
from __future__ import annotations

import unittest

from arity.spirals import ProceduralSpiralRenderer, SpiralSpinners


class TestSpirals(unittest.TestCase):
    def test_vogel_sunflower_rendering(self):
        output = ProceduralSpiralRenderer.render_vogel_sunflower(num_seeds=50, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("●", output)
        lines = output.splitlines()
        self.assertEqual(len(lines), 15)

    def test_logarithmic_nautilus_rendering(self):
        output = ProceduralSpiralRenderer.render_logarithmic_nautilus(turns=2.0, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("●", output)

    def test_rose_flower_rendering(self):
        output = ProceduralSpiralRenderer.render_rose_flower(petals=5, width=31, height=15)
        self.assertIsInstance(output, str)
        self.assertIn("✿", output)

    def test_arity_spinners(self):
        # Unary (1 model)
        s1 = SpiralSpinners.get_arity_spinner(arity=1, frame_idx=0)
        self.assertIn("arity:1", s1)

        # Binary (2 models / Tag-team)
        s2 = SpiralSpinners.get_arity_spinner(arity=2, frame_idx=0)
        self.assertIn("arity:2", s2)
        self.assertIn("tag-team", s2)

        # Multipolar (4 models / Swarm)
        s4 = SpiralSpinners.get_arity_spinner(arity=4, frame_idx=0)
        self.assertIn("arity:4", s4)
        self.assertIn("terrarium", s4)


if __name__ == "__main__":
    unittest.main()
