"""Unit tests for arity Skills engine, Secretary, Engineer, and Python Developer hierarchy."""
import unittest

from arity.roles import (
    ENGINEER_ROLE,
    PYTHON_DEVELOPER_ROLE,
    SCOUT_ROLE,
    SECRETARY_ROLE,
    TESTER_ROLE,
    RoleRegistry,
)
from arity.scorecard import Scorecard
from arity.skills import FIRECRAWL_SKILL, PYTHON_DEVELOPER_SKILL, Skill, SkillRegistry
from arity.tiers import BriefCompiler


class TestSkillsAndHierarchy(unittest.TestCase):
    def test_skill_registry_compilation(self):
        reg = SkillRegistry()
        py_skill = reg.get("python-development")
        self.assertIsNotNone(py_skill)
        self.assertIn("Python 3.13", py_skill.instructions)

        compiled = reg.compile_prompt(["python-development", "firecrawl-developer-index"])
        self.assertIn("Skill: python-development", compiled)
        self.assertIn("Skill: firecrawl-developer-index", compiled)

    def test_role_resolution_hierarchy(self):
        registry = RoleRegistry()

        # Secretary resolution
        r1 = registry.resolve("hi secretary, how are we doing today?")
        self.assertEqual(r1.name, "secretary")
        self.assertEqual(r1.tier, 0)

        # Python developer resolution
        r2 = registry.resolve("write a python script with pytest tests")
        # Lead engineer resolution
        r3 = registry.resolve("plan the system architecture and technical spec")
        self.assertEqual(r3.name, "engineer")
        self.assertEqual(r3.tier, 1)

    def test_brief_compiler_injects_skills(self):
        compiler = BriefCompiler()
        brief = compiler.assemble(
            role=PYTHON_DEVELOPER_ROLE,
            task="Build a math helper",
        )
        self.assertIn("Skill: python-development", brief.system_prompt)
        self.assertIn("Skill: pytest-tdd", brief.system_prompt)

    def test_scorecard_skill_scoped_ratings(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from arity.handlers import JsonlRecordStore

        with TemporaryDirectory() as tmpdir:
            store = JsonlRecordStore(root=Path(tmpdir) / "records")
            scorecard = Scorecard(store=store)
            scorecard.record_verdict(
                role="python_developer",
                model="test-candidate",
                task_id="t1",
                verdict="success",
                skills=["python-development", "pytest-tdd"],
            )

            role_standing = scorecard.get_standing("python_developer", "test-candidate")
            skill_standing = scorecard.get_standing("skill:python-development", "test-candidate")
            self.assertEqual(role_standing, 11.0)
            self.assertEqual(skill_standing, 11.0)
if __name__ == "__main__":
    unittest.main()
