import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent

def test_line_count():
    py_files = list(ROOT.glob("*.py"))
    total_lines = 0
    file_counts = {}
    for f in py_files:
        if f.name.startswith("test_"):
            continue
        lines = len(f.read_text(encoding="utf-8").splitlines())
        file_counts[f.name] = lines
        total_lines += lines
    print("\nLine counts:")
    for name, cnt in sorted(file_counts.items()):
        print(f"  {name:15s}: {cnt:4d} lines")
    print(f"Total across {len(file_counts)} modules: {total_lines} lines")
    assert total_lines < 1500, f"Total lines {total_lines} exceeds 1500"

def test_role_denials_and_brief_leaks():
    from roles import Role, Access, Denied, BriefLeak
    from tiers import Tiers, Task
    from store import Store

    store = Store(ROOT / "test_store")
    tiers = Tiers(store)

    role = Role(
        "leaf",
        tier=2,
        allow=Access(tools=frozenset({"toolA"})),
        deny=Access(names=frozenset({"Asa"}), paths=frozenset({"secret_path"})),
    )

    with pytest.raises(Denied):
        role.enforce("names", "Asa")

    with pytest.raises(Denied):
        role.enforce("names", "this mentions asa in sentence")

    with pytest.raises(Denied):
        role.enforce("tools", "toolB")

    task_with_leak = Task("t1", "Do something with Asa", "project")
    with pytest.raises(BriefLeak):
        tiers.assemble(role, task_with_leak)

    task_clean = Task("t2", "Do clean work", "project")
    brief = tiers.assemble(role, task_clean)
    assert "Do clean work" in brief

def test_run_demo():
    import demo
    demo.main()
