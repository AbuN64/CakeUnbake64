"""pyproject declares abucache by name only: a commit pin strands unbake behind the abucache timing consumers use."""

import re
import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _requirements(dependencies: list[str]) -> list[str]:
    return [r for r in dependencies if re.match(r"abucache(?![\w.-])", r.strip())]


def test_pyproject_declares_abucache_without_a_commit_pin():
    dependencies = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    assert _requirements(dependencies) == ["abucache"]


def test_the_check_refuses_a_commit_pin_on_abucache():
    pinned = ["click==8.5.0", "abucache @ git+https://example.invalid/cache-64@" + "4" * 40]
    assert _requirements(pinned) != ["abucache"]
    assert _requirements(["abucache==0.7.0"]) != ["abucache"]
