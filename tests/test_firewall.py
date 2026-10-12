"""Truth firewall: ground code must never import the simulator (it may only see telemetry).

Parses every module with `ast`, resolves relative imports to absolute module
names, and fails if a forbidden package is reachable by a direct import.
"""
import ast
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parent.parent / "aerofire"

RULES = {
    # package prefix          : forbidden import prefixes
    "aerofire.ground": ["aerofire.sim"],
    "aerofire.physics": ["aerofire.sim", "aerofire.ground", "aerofire.api"],
    "aerofire.telemetry": ["aerofire.ground"],
}


def module_name(path: Path) -> str:
    rel = path.relative_to(PKG_ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    mod = module_name(path)
    is_pkg = path.name == "__init__.py"
    package = mod if is_pkg else mod.rsplit(".", 1)[0]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            found.add(target)
            found.update(f"{target}.{a.name}" for a in node.names)  # `from .. import sim`
    return found


def violations(path: Path) -> list[str]:
    mod = module_name(path)
    bad = []
    for prefix, forbidden in RULES.items():
        if mod == prefix or mod.startswith(prefix + "."):
            for imp in imports_of(path):
                for f in forbidden:
                    if imp == f or imp.startswith(f + "."):
                        bad.append(f"{mod} imports {imp}")
    return bad


def all_modules():
    return sorted(PKG_ROOT.rglob("*.py"))


@pytest.mark.parametrize("path", all_modules(), ids=lambda p: module_name(p))
def test_no_forbidden_imports(path):
    assert violations(path) == []


def test_checker_catches_violations(tmp_path, monkeypatch):
    """The checker itself must flag absolute, relative and `from .. import sim` forms."""
    fake_root = tmp_path / "aerofire"
    (fake_root / "ground").mkdir(parents=True)
    samples = {
        "a.py": "import aerofire.sim.mission\n",
        "b.py": "from ..sim.mission import MissionSim\n",
        "c.py": "from .. import sim\n",
        "d.py": "from ..physics import tank\n",  # allowed
    }
    for name, src in samples.items():
        (fake_root / "ground" / name).write_text(src)
    monkeypatch.setattr(__import__(__name__), "PKG_ROOT", fake_root)
    flagged = {p.name for p in (fake_root / "ground").glob("*.py") if violations(p)}
    assert flagged == {"a.py", "b.py", "c.py"}


def test_ground_package_exists_and_is_checked():
    ground = [p for p in all_modules() if module_name(p).startswith("aerofire.ground")]
    assert any(p.name == "station.py" for p in ground)
