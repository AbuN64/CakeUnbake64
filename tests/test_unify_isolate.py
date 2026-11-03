"""The bad-object bisection of unify over the reduced bc0d034ac slice with many innocent names in the one owning
header (the shape of the wave1c CLASH.tsv bisection: a header every unit includes, one unit whose objects break).
The make seam counts the sequential makes and the units they compile."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from test_unify_definition import BARE_CALLER, CALLER, DATA, DEFINITION, FUNCTION, FUNCTION_HEADER, World, invoke

from unbake import typeunify

INNOCENT = 16
# What the trial-per-half bisection cost before it tested only the objects its half touches: every trial rebuilt the
# bad objects, so each of its halvings was one make of them, and none was skipped.
SERIAL_MAKES = 9  # 48 compiled units: makes of [16, 10, 2, 2, 2, 2, 2, 2, 10] units
# One search over the 16-name slice below: baseline, the rewrites together (round 1), 14 isolate makes, the one
# verify, the final drift check. The serial passes made 29 rounds and held innocent names.
ROUNDS = 16
UNITS = 70


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


class Culprits(World):
    """A world whose object differs when the header holds the rewrite (`extern int <name>;`) of one of its culprits."""

    def __init__(self, root: Path, culprits: dict[str, set[str]]) -> None:
        super().__init__(root)
        self.culprits = culprits  # unit -> the names whose rewrite changes its object

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(targets))
        header = (self.root / FUNCTION_HEADER).read_text()
        for target in targets:
            unit = target.split("/", 2)[2][:-2]
            differs = any(f"extern int {n};" in header for n in self.culprits.get(unit, ()))
            out = self.root / target
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(target + (" differs" if differs else ""))
        return 0, ""


def test_every_culprit_of_different_groups_is_found_in_one_search_and_one_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three culprits in two bad units, each unit using half of 16 names: the serial search was one pass per culprit,
    each with its bisection rounds and a full verify; one search finds them all, the halves of a level share a make
    and one verify closes it. Counts of makes and units, never wall time."""
    world = Culprits(tmp_path, {BARE_CALLER: {"Aaa03", "Aaa05"}, CALLER: {"Aaa12"}})
    names = [f"Aaa{i:02d}" for i in range(INNOCENT)]
    definition, header = tmp_path / DEFINITION, tmp_path / FUNCTION_HEADER
    definition.write_text(definition.read_text() + "".join(f"int {n} = 0;\n" for n in names))
    header.write_text(header.read_text() + "".join(f"extern long {n};\n" for n in names))
    for path, used in ((BARE_CALLER, names[: INNOCENT // 2 + 1]), (CALLER, names[INNOCENT // 2 + 1 :])):
        user = tmp_path / path
        user.write_text(
            '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\n'
            + user.read_text()
            + "int uses_"
            + path[-8:-4]
            + "(void) { return "
            + " + ".join(used)
            + "; }\n"
        )
    stages: list[str] = []
    real_stage = typeunify.stages.stage

    def stage(name: str) -> Any:
        stages.append(name)
        return real_stage(name)

    monkeypatch.setattr(typeunify.stages, "stage", stage)
    result = invoke(world, monkeypatch)
    # (a) the result is the serial search's: every culprit held, every other name unified, each held on its objects
    assert set(result["held"]) == {"Aaa03", "Aaa05", "Aaa12"}
    assert set(result["unified"]) == {DATA, FUNCTION, *names} - {"Aaa03", "Aaa05", "Aaa12"}
    # (b) one search: the passes of the serial search (three culprits, nine makes each at this size) are gone
    assert result["rounds"] == len(world.calls) - 2 == ROUNDS
    assert sum(len(c) for c in world.calls) <= UNITS
    assert stages.count("unify.isolate.verify") == 1


EXTRA = 12


def _wide_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunk: int) -> tuple[dict[str, Any], Culprits, int]:
    """16 names all used by 12 extra callers (the shape of a header every unit includes), three culprits over shared
    objects: one breaks every caller, one two of them, one a single one. `chunk` is the first isolate make's size."""
    names = [f"Aaa{i:02d}" for i in range(INNOCENT)]
    callers = [f"SOURCE/EXTRA/EXTRA{i:02d}.CPP" for i in range(EXTRA)]
    culprits = {c: {"Aaa03"} for c in callers}
    culprits[callers[5]].add("Aaa12")
    culprits[callers[6]].add("Aaa12")
    culprits[callers[10]].add("Aaa07")
    world = Culprits(tmp_path, culprits)
    definition, header = tmp_path / DEFINITION, tmp_path / FUNCTION_HEADER
    definition.write_text(definition.read_text() + "".join(f"int {n} = 0;\n" for n in names))
    header.write_text(header.read_text() + "".join(f"extern long {n};\n" for n in names))
    for path in callers:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(
            '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\nint use_'
            + path[-6:-4]
            + "(void) { return "
            + " + ".join(names)
            + "; }\n"
        )
    stages: list[str] = []
    real_stage = typeunify.stages.stage

    def stage(name: str) -> Any:
        stages.append(name)
        return real_stage(name)

    monkeypatch.setattr(typeunify.stages, "stage", stage)
    monkeypatch.setattr(typeunify, "FIRST_CHUNK", chunk)
    result = invoke(world, monkeypatch)
    return result, world, stages.count("unify.isolate.verify")


def test_a_trial_stops_at_its_first_failing_chunk_and_holds_the_same_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The search with early-stopping trials holds exactly the names of the search whose trials build every object
    they touch, from fewer object builds, and closes with a bounded number of verifies. Counts of builds only."""
    full, full_world, full_verifies = _wide_search(tmp_path / "full", monkeypatch, 10**9)
    early, early_world, early_verifies = _wide_search(tmp_path / "early", monkeypatch, 2)
    assert set(full["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    assert set(early["held"]) == set(full["held"]) and set(early["unified"]) == set(full["unified"])
    assert sum(map(len, early_world.calls)) < sum(map(len, full_world.calls))
    assert early_verifies <= full_verifies + 1
