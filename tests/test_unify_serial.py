"""The serial work of unify's stages outside make, over the reduced bc0d034ac slice with the shape of the rig run
(many objects fail in one make; a bisection ends on a state a trial already built). Counts of scans and rebuilt
objects, never wall time."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from test_unify_definition import BARE_CALLER, CALLER, DEFINITION, FUNCTION, FUNCTION_HEADER, World, invoke

from unbake import compilerrors, typeunify


class Noisy(World):
    """A world whose failed objects print compiler errors (`<unit>.i:<line>: error: ...`) among many warnings."""

    def make(self, targets: Any) -> tuple[int, str]:
        code, log = super().make(targets)
        extra = "".join(f"{t[:-2]}.i:{n}: warning: noise {n}\n" for t in targets for n in range(5)) + "".join(
            f"{t[:-2]}.i:7: error: boom in {t[:-2].rsplit('/', 1)[1]}\n" for t in targets if t in log
        )
        return code, log + extra


class Scans:
    """Counts the regex scans of the make log: one pass per make is linear, one per failed object is not."""

    def __init__(self, real: Any) -> None:
        self.real, self.count = real, 0

    def finditer(self, text: str, *a: Any) -> Any:
        self.count += len(text) > 200
        return self.real.finditer(text, *a)

    def findall(self, text: str, *a: Any) -> Any:
        self.count += len(text) > 200
        return self.real.findall(text, *a)


def test_failed_objects_read_their_errors_in_one_pass_over_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = Noisy(tmp_path)
    world.broken |= {DEFINITION, CALLER, BARE_CALLER}
    scans = Scans(compilerrors.ERROR)
    monkeypatch.setattr(compilerrors, "ERROR", scans)
    real_findall = re.findall

    def findall(pattern: Any, text: str, *a: Any) -> Any:
        scans.count += isinstance(text, str) and len(text) > 200 and "\\.i:" in str(pattern)
        return real_findall(pattern, text, *a)

    monkeypatch.setattr(compilerrors.re, "findall", findall)
    result = invoke(world, monkeypatch)
    errors = {o["error"] for o in result["objects"].values()}
    assert len(result["objects"]) == 6  # three sources in two versions fail in one make
    assert any("error: boom in achieve_match_setup.CPP" in e for e in errors) and not any("noise" in e for e in errors)
    # the logs scanned for errors: one per make that had failures, not one per failed object
    assert scans.count <= len(world.calls)


def test_first_errors_keeps_three_lines_per_object_from_one_scan() -> None:
    log = "".join(f"build/v/a.c.i:{n}: error: e{n}\n" for n in range(5)) + "build/v/b.c.i:2: warning: w\n"
    got = compilerrors.first_errors(log, ["build/v/a.c.o", "build/v/b.c.o", "build/v/c.c.o"])
    assert got == {
        "build/v/a.c.o": "error: e0 | error: e1 | error: e2",
        "build/v/b.c.o": "no compiler error in the log",
        "build/v/c.c.o": "no compiler error in the log",
    }


def test_verify_skips_objects_a_passing_trial_built_with_the_remaining_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Innocent names sort before the culprit, so the last bisection trial (everything but the culprit) is exactly the
    state after the culprit is held: the verify of that state is that trial and builds nothing."""
    world = World(tmp_path)
    names = [f"Aaa{i:02d}" for i in range(3)]
    definition, header, caller = tmp_path / DEFINITION, tmp_path / FUNCTION_HEADER, tmp_path / CALLER
    definition.write_text(definition.read_text() + "".join(f"int {n} = 0;\n" for n in names))
    header.write_text(header.read_text() + "".join(f"extern long {n};\n" for n in names))
    use = "int user_of_names(void) { return " + " + ".join(names) + "; }\n"  # the bad object uses every name
    caller.write_text('#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\n' + caller.read_text() + use)
    world.different.add(CALLER)
    result = invoke(world, monkeypatch)
    assert set(result["held"]) == {FUNCTION} and set(names) <= set(result["unified"])
    bad = len({typeunify.target((version, CALLER)) for version in world.units()})
    # baseline, the rewrites together, two bisection trials of the bad objects (four candidates), the final drift
    # check: the verify of the state the second trial built is not made again (a0a36e6 made it: six builds)
    assert [len(c) for c in world.calls][1:4] == [10, bad, bad]
    assert len(world.calls) == 5
    assert result["verified"] > bad


def test_the_out_table_holds_only_what_was_decided_so_two_runs_write_the_same_table():
    from unbake import typeunify

    def document(secs: float) -> dict:
        return {
            "seconds": {"B": secs, "A": secs * 2},
            "unified": {"A": {"type": "void(int)"}},
            "unresolved": {"B": {"int": "x"}},
        }

    first, second = typeunify._rows(document(1.5)), typeunify._rows(document(2.5))
    assert first == second
    assert first.splitlines() == [
        "name\tstatus\ttype\tfailing per candidate type",
        "A\tunified\tvoid(int)\t",
        "B\tunresolved\t\tint => x",
    ]
