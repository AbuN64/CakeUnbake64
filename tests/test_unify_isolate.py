"""The bad-object bisection of unify over the reduced bc0d034ac slice with many innocent names in the one owning
header (the shape of the wave1c CLASH.tsv bisection: a header every unit includes, one unit whose objects break).
The make seam counts the sequential makes and the units they compile."""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import pairwise
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
ROUNDS = 15
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


def test_round_verifies_rebuild_only_what_changed_and_the_closing_check_is_the_one_full_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression slice of wave1c unify run a: every round rebuilt the whole set of tested objects (3638, 3329, 3329,
    3324, 3329 compiles) for each culprit round. Here the same shape (a header every caller includes, culprits that
    break all callers, two callers or one): with early-stopping trials (rounds that leave objects undecided) the
    held names are the same, and no make after the rewrites' wave rebuilds every object more than twice."""
    early, world, _ = _wide_search(tmp_path, monkeypatch, 2)
    assert set(early["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    assert all(early["held"][n] for n in early["held"])
    wave = len(world.calls[1])
    full = [c for c in world.calls[2:] if len(c) >= wave]
    assert len(full) <= 2 and len(world.calls[-1]) >= wave  # the closing check is one of them
    # the verifies between rounds stop at their first failing chunk: none is a make of every tested object
    assert all(len(c) < wave for c in world.calls[2:-1])


class Masked(Culprits):
    """An object differs while Aaa03 is in the header, or while Aaa07 and Aaa12 both are: a culprit that only shows
    once the first is out and that no half-trial on its own sees."""

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(targets))
        header = (self.root / FUNCTION_HEADER).read_text()
        has = {n for n in ("Aaa03", "Aaa07", "Aaa12") if f"extern int {n};" in header}
        for target in targets:
            differs = "Aaa03" in has or {"Aaa07", "Aaa12"} <= has
            out = self.root / target
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(target + (" differs" if differs and "EXTRA" in target else ""))
        return 0, ""


def test_a_culprit_masked_by_another_is_found_by_the_closing_check_and_searched_once_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = [f"Aaa{i:02d}" for i in range(INNOCENT)]
    callers = [f"SOURCE/EXTRA/EXTRA{i:02d}.CPP" for i in range(EXTRA)]
    world = Masked(tmp_path, {})
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
    monkeypatch.setattr(typeunify, "FIRST_CHUNK", 2)
    result = invoke(world, monkeypatch)
    assert "Aaa03" in result["held"] and result["complete"] is True
    assert not [n for n in result["unified"] if n in result["held"]]
    wave = len(world.calls[1])
    assert len([c for c in world.calls[2:] if len(c) >= wave]) <= 2 + 1


SLICE = Path(__file__).parent / "data" / "unify_isolate_slice.txt"
COMPILES = re.compile(r"make\.front: 0 of (\d+) compiles from the cache")


class Watched(Culprits):
    """Culprits that records the state (the rewrites in the header) each object is built in, per stage."""

    def __init__(self, root: Path, culprits: dict[str, set[str]]) -> None:
        super().__init__(root, culprits)
        self.stage = ""
        self.built: list[tuple[str, str, frozenset[str]]] = []  # (stage, object, extern names in the header)

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        header = (self.root / FUNCTION_HEADER).read_text()
        state = frozenset(re.findall(r"extern int (\w+);", header))
        self.built += [(self.stage, t, state) for t in targets]
        return super().make(targets)


def test_isolate_never_builds_an_object_twice_in_the_state_it_was_already_built_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression slice of dkumJOKlQbjJ run 8 (tests/data/unify_isolate_slice.txt: the makes of the log, each with its
    compile count, restarting the same descent a second time): the isolate stage re-made objects it had decided, 210
    trials of one make each. Here the wide search (callers all including the header, three culprits): within the
    isolate stages no object is built twice in one rewrite state, nor in the state the first rebuild built it in; the
    held names stay the same, and the closing check is the one full verify."""
    counts = [int(m[1]) for m in map(COMPILES.search, SLICE.read_text().splitlines()) if m]
    assert counts[:10] == [0, 3637, 1899, 1019, 633, 325, 245, 220, 190, 25] or counts[1:3] == [3637, 1899]
    assert sum(1 for a, b in pairwise(counts) if b > a) <= 3  # the slice restarts its descent
    names = [f"Aaa{i:02d}" for i in range(INNOCENT)]
    callers = [f"SOURCE/EXTRA/EXTRA{i:02d}.CPP" for i in range(EXTRA)]
    culprits = {c: {"Aaa03"} for c in callers}
    culprits[callers[5]].add("Aaa12")
    culprits[callers[10]].add("Aaa07")
    world = Watched(tmp_path, culprits)
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
    real_stage = typeunify.stages.stage
    seen: list[str] = []

    def stage(name: str) -> Any:
        seen.append(name)
        world.stage = name
        return real_stage(name)

    monkeypatch.setattr(typeunify.stages, "stage", stage)
    monkeypatch.setattr(typeunify, "FIRST_CHUNK", 2)
    result = invoke(world, monkeypatch, result=str(tmp_path / "scratch" / "partial.json"))
    assert set(result["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    isolate = [(o, s) for st, o, s in world.built if st.startswith("unify.isolate") or st == "unify.write"]
    first = {(o, s) for st, o, s in world.built if st == "unify.rebuild"}
    assert len(isolate) == len(set(isolate)) and not (set(isolate) & first)
    # every second of the run is in a stage line: the time between trials (setup, writing the files, saving the
    # partial result) has its own
    assert {"unify.baseline", "unify.census", "unify.setup", "unify.plan", "unify.rebuild", "unify.write"} <= set(seen)
    assert {"unify.isolate", "unify.isolate.verify", "unify.save"} <= set(seen)


def _sparse_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remembered: bool) -> tuple[list[str], int]:
    """24 names, 20 callers each using 3 neighbouring names (objects reached by few names, so a state recurs between
    rounds), six culprits. Answers the held names and the makes; `remembered` false makes every trial build again."""
    count = 24
    names = [f"Aaa{i:02d}" for i in range(count)]
    callers = [f"SOURCE/EXTRA/EXTRA{i:02d}.CPP" for i in range(20)]
    culprits: dict[str, set[str]] = {c: set() for c in callers}
    for n, c in ((3, 2), (3, 3), (7, 6), (12, 11), (12, 12), (15, 14), (20, 17)):
        culprits[callers[c]].add(names[n])
    world = Culprits(tmp_path, culprits)
    definition, header = tmp_path / DEFINITION, tmp_path / FUNCTION_HEADER
    definition.write_text(definition.read_text() + "".join(f"int {n} = 0;\n" for n in names))
    header.write_text(header.read_text() + "".join(f"extern long {n};\n" for n in names))
    for i, path in enumerate(callers):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        used = " + ".join(names[(i + j) % count] for j in range(3))
        (tmp_path / path).write_text(
            '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\nint use_'
            + path[-6:-4]
            + f"(void) {{ return {used}; }}\n"
        )
    real = typeunify._decide
    if not remembered:
        monkeypatch.setattr(typeunify, "_decide", lambda r, s, t, b, e=None, known=None: real(r, s, t, b, e))
    monkeypatch.setattr(typeunify, "FIRST_CHUNK", 2)
    result = invoke(world, monkeypatch)
    return sorted(result["held"]), len(world.calls)


def test_remembered_states_cost_no_make_and_hold_the_same_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An object already built in the very state a trial gives it is not built again: fewer makes, same names."""
    held, makes = _sparse_search(tmp_path / "a", monkeypatch, True)
    other, again = _sparse_search(tmp_path / "b", monkeypatch, False)
    assert held == other and len(held) == 6
    assert makes < again
