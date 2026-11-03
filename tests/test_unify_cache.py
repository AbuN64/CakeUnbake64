"""Why unify's isolate trials miss the compile cache, and what one search level costs in makes.
The slice is the real `make.front: miss compile` lines of rig-20261103T091155Z (CakeUnbake64 dd1a401) for three
objects that every isolate trial rebuilt: the key of a compile is a function of its preprocessed text, so a miss is a
new rewrite state of that object (a first build), never the same state under a changed key."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_unify_definition import BARE_CALLER, CALLER, DEFINITION, FUNCTION_HEADER, invoke
from test_unify_isolate import Culprits

MISS = re.compile(r"make\.front: miss compile (\S+) compiler=(\w+) head=(\w+) text=(\w+) key=(\w+)")
SLICE = Path(__file__).parent / "data" / "unify_miss_slice.txt"
NAMES = 16


def test_slice_misses_are_new_states_never_an_unstable_key() -> None:
    states: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for text in SLICE.read_text().splitlines():
        found = MISS.fullmatch(text)
        assert found, text
        states[found[1]].append(found.groups()[1:])  # type: ignore[arg-type]
    assert len(states) == 3 and sum(map(len, states.values())) == 23
    for obj, rows in states.items():
        # the compiler and the header part of the key do not move between the trials of one object
        assert len({r[0] for r in rows}) == 1 and len({r[1] for r in rows}) == 1, obj
        by_text: dict[str, set[str]] = defaultdict(set)
        for _, _, text, key in rows:
            by_text[text].add(key)
        # one text, one key (stable); and no text is built twice (each miss is a first build of a new state)
        assert all(len(keys) == 1 for keys in by_text.values()), obj
        assert len(by_text) == len(rows), obj


class Cached(Culprits):
    """A world with the compile cache: an object built in a state (its target and the header it was built with) is a
    hit the second time."""

    def __init__(self, root: Path, culprits: dict[str, set[str]]) -> None:
        super().__init__(root, culprits)
        self.seen: set[tuple[str, str]] = set()
        self.hits = self.compiles = 0
        self.waves: list[set[str]] = []

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        state = (self.root / FUNCTION_HEADER).read_text()
        for target in targets:
            if (target, state) in self.seen:
                self.hits += 1
            else:
                self.compiles += 1
                self.seen.add((target, state))
        self.waves.append({t.split("/", 2)[2][:-2] for t in targets})
        return super().make(targets)


def disjoint_world(root: Path) -> Cached:
    world = Cached(root, {BARE_CALLER: {"Aaa03", "Aaa05"}, CALLER: {"Aaa12"}})
    names = [f"Aaa{i:02d}" for i in range(NAMES)]
    (root / DEFINITION).write_text((root / DEFINITION).read_text() + "".join(f"int {n} = 0;\n" for n in names))
    (root / FUNCTION_HEADER).write_text(
        (root / FUNCTION_HEADER).read_text() + "".join(f"extern long {n};\n" for n in names)
    )
    for path, used in ((BARE_CALLER, names[: NAMES // 2]), (CALLER, names[NAMES // 2 :])):
        user = root / path
        user.write_text(
            '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\n'
            + user.read_text()
            + f"int uses_{path[-8:-4]}(void) {{ return {' + '.join(used)}; }}\n"
        )
    return world


def test_trials_of_disjoint_objects_share_a_make_and_a_repeated_state_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = disjoint_world(tmp_path)
    result = invoke(world, monkeypatch)
    assert set(result["held"]) == {"Aaa03", "Aaa05", "Aaa12"}
    units = {BARE_CALLER, CALLER}
    isolate = world.waves[2:-2]  # not the baseline, the rewrites together, the isolate verify and the final check
    both = [w for w in isolate if units <= w]
    only = [w for w in isolate if len(w & units) == 1]
    # the trials of the two units' culprit searches that are of one level build in one make: most makes hold both
    assert len(both) >= len(only) and both
    # each unit alone would need its own bisection (3 levels, 4 makes with its two culprits): the shared makes beat
    # the sum of both, which is what serial trials cost
    assert len(isolate) < 12
    # a state built twice is a hit: the final check and the verify rebuild states the search built before
    assert world.hits > 0 and world.hits + world.compiles == sum(len(c) for c in world.calls)
