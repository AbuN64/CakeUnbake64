"""A project loaded by one test cannot change the next test's layout, regardless of worker scheduling."""

from pathlib import Path

import pytest


def test_loaded_layout_is_private_to_each_test(pytester: pytest.Pytester) -> None:
    tests = Path(__file__).parent
    pytester.syspathinsert(tests)
    pytester.makeconftest((tests / "conftest.py").read_text())
    pytester.makepyfile(
        test_sequence="""
from unbake import config, guards, repo
from test_config import BT, NAMED_LAYOUT, _load


def test_load_named_layout(tmp_path):
    _load(tmp_path, BT + "\\n" + NAMED_LAYOUT, tracked=[])
    assert config.header_roots() == ("Include", "leftover/Include", "sdk/include")
    assert guards.name("Include/os/os.h") == "OS_OS_H"


def test_next_project_uses_its_own_defaults():
    defaults = config.load_resource("repo.toml")["layout"]
    assert config.header_roots() == tuple(defaults["header_roots"])
    assert config.source_roots() == tuple(defaults["source_roots"])
    assert all(config.root(kind) == defaults[f"{kind}_root"] for kind in config.BUILD_ROOTS)
    assert guards.name("include/os/os.h") == "OS_OS_H"
    assert guards.name("include/123.H") == "HEADER_123_H"
    assert repo.header() == "include/include_asm.h"
"""
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:xdist", "test_sequence.py")
    result.assert_outcomes(passed=2)
