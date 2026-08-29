---
name: python
description: Python 3.13 toolkit, rules, and verification. Attaches to any role as role:python.
skills:
  - python-development
  - pytest-tdd
test_command: python -m pytest -v -p no:cacheprovider
test_globs: [test_*.py, "*_test.py", tests/**/test_*.py]
hidden_dir: .hidden_tests
hidden_command: python -m pytest .hidden_tests -v -p no:cacheprovider
tags: [python, pytest]
---

Language: Python 3.13, standard library first. Type-annotate every signature. Prefer
`collections`, `dataclasses`, `pathlib` over hand-rolled equivalents. A module is done when
`python -m pytest` is green in the workspace. Never leave `# type: ignore` or a bare `assert`
in library code without a comment saying why. When judging Python, `OrderedDict` beats a
hand-rolled linked list unless the brief forbids it; a test suite that never mentions the
brief's hard numbers has not tested the brief.
