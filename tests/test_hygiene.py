"""tests/hygiene.py: a unit test that spawns a real build tool or runs past its seconds bound fails, even when the code
under test catches the refusal; a stub the test wrote is no real tool; the integration set runs apart."""

from __future__ import annotations

import sys
from pathlib import Path

import hygiene
import pytest

INNER = """
import subprocess
import time

import pytest


def test_spawns_make():
    subprocess.run(["make", "--version"], capture_output=True)


def test_catches_the_refusal_of_a_compiler():
    try:
        subprocess.run(["cc", "--version"], capture_output=True)
    except OSError:
        pass  # the code under test swallows it: the test fails all the same


@pytest.mark.seconds(0.05)
def test_sleeps_past_its_bound():
    time.sleep(0.1)


@pytest.mark.seconds(0.05)
def test_burns_cpu_past_its_bound():
    end = time.process_time() + 0.15
    while time.process_time() < end:
        pass


def test_runs_a_stub_it_wrote(tmp_path):
    stub = tmp_path / "make"
    stub.write_text("#!/bin/sh\\nexit 0\\n")
    stub.chmod(0o755)
    assert subprocess.run([str(stub)]).returncode == 0


def test_inside_its_bound():
    pass


@pytest.mark.integration("make is the code under test")
def test_of_the_integration_set():
    pass
"""


@pytest.fixture
def inner(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyfile(test_inner=INNER)
    pytester.syspathinsert(Path(hygiene.__file__).parent)
    return pytester


def test_a_unit_test_that_spawns_a_build_tool_or_outruns_its_bound_fails(inner: pytest.Pytester) -> None:
    done = inner.runpytest_inprocess("-p", "hygiene", "-p", "no:cacheprovider", "-p", "no:xdist")
    done.assert_outcomes(passed=2, failed=4, deselected=1)
    text = done.stdout.str()
    assert "spawned the real build tool" in text and "make" in text and "cc" in text
    assert text.count("over the test's bound of 0.05 s") == 2  # the sleeper and the burner


def test_the_integration_set_runs_apart(inner: pytest.Pytester) -> None:
    done = inner.runpytest_inprocess("-p", "hygiene", "-p", "no:cacheprovider", "-p", "no:xdist", "--integration")
    done.assert_outcomes(passed=1, deselected=6)


def test_an_integration_mark_needs_its_reason(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("import pytest\n\n@pytest.mark.integration\ndef test_x():\n    pass\n")
    pytester.syspathinsert(Path(hygiene.__file__).parent)
    done = pytester.runpytest_inprocess("-p", "hygiene", "-p", "no:cacheprovider", "-p", "no:xdist")
    assert done.ret == pytest.ExitCode.USAGE_ERROR


@pytest.mark.parametrize(
    "argv, real",
    [
        (["make", "-j4"], True),
        (["mips-linux-gnu-objcopy", "-O", "binary"], True),
        (["x86_64-linux-gnu-gcc-13", "-c"], True),
        (["git", "status"], True),
        (
            [sys.executable, "-I", str(Path(hygiene.__file__).parents[1] / "src/unbake/resources/make/fill_data.py")],
            True,
        ),
        (["sh", "-c", "cat a && make -s all"], True),
        (["sh", "-c", "echo build it as before"], False),
        ([sys.executable, "-c", "print(1)"], False),
        (["true"], False),
    ],
)
def test_a_real_build_tool_is_told_from_any_other_program(argv: list[str], real: bool) -> None:
    assert bool(hygiene.refusal(argv, {}, None)) is real


def test_contention_is_one_when_idle_and_scales_cpu_but_not_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hygiene, "_contention_at", [float("-inf"), 1.0])
    monkeypatch.setattr(hygiene, "_fastest", [1.0])
    monkeypatch.setattr(hygiene, "_spin", lambda: 3.0)  # the host runs the spin three times slower than its fastest
    assert hygiene.contention() == 3.0
    monkeypatch.setattr(hygiene, "_contention_at", [float("-inf"), 1.0])
    monkeypatch.setattr(hygiene, "_spin", lambda: 100.0)
    assert hygiene.contention() == hygiene.MAX_CONTENTION  # a test that slow is slow, whatever the load
    monkeypatch.setattr(hygiene, "_contention_at", [float("-inf"), 1.0])
    monkeypatch.setattr(hygiene, "_spin", lambda: 0.5)
    assert hygiene.contention() == 1.0  # never below idle
