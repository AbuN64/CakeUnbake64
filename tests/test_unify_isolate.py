"""The bad-object bisection of unify over the reduced bc0d034ac slice with many innocent names in the one owning
header (the shape of the wave1c CLASH.tsv bisection: a header every unit includes, one unit whose objects break).
The make seam counts the sequential makes and the units they compile."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_unify_definition import CALLER, DATA, DEFINITION, FUNCTION, FUNCTION_HEADER, World, invoke

from unbake import typeunify

INNOCENT = 16
# What the trial-per-half bisection cost before it tested only the objects its half touches: every trial rebuilt the
# bad objects, so each of its halvings was one make of them, and none was skipped.
SERIAL_MAKES = 9  # 48 compiled units: makes of [16, 10, 2, 2, 2, 2, 2, 2, 10] units


def test_isolate_skips_trials_whose_half_touches_no_bad_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    names = [f"Aaa{i:02d}" for i in range(INNOCENT)]
    definition, header, caller = tmp_path / DEFINITION, tmp_path / FUNCTION_HEADER, tmp_path / CALLER
    definition.write_text(definition.read_text() + "".join(f"int {n} = 0;\n" for n in names))
    header.write_text(header.read_text() + "".join(f"extern long {n};\n" for n in names))
    caller.write_text('#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\n' + caller.read_text())
    world.different.add(CALLER)
    result = invoke(world, monkeypatch)
    # (a) the result is the serial bisection's: the one culprit held, every innocent name unified
    assert set(result["unified"]) == {DATA, *names} and set(result["held"]) == {FUNCTION}
    objects = {typeunify.target((version, CALLER)) for version in world.units()}
    assert result["objects"] == {obj: {"error": "object differs", "names": [FUNCTION]} for obj in objects}
    # (b) fewer sequential makes, and the units compiled at the seam
    assert len(world.calls) < SERIAL_MAKES
    assert sum(len(c) for c in world.calls) < 48
