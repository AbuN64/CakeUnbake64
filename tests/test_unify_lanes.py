"""Concurrent isolate waves: each wave builds in a lane of its own, `width` at a time, and the search holds the names
the serial one holds. Counts and overlaps only, never wall time."""

from __future__ import annotations

import contextlib
import shutil
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_unify_isolate import Culprits, _wide_search

from unbake import process, typeunify
from unbake.contracts import NativeResult
from unbake.typeunify import Seams

# trials per isolate round of wave1c unify run a (rig-20261103T101825Z): 210 one-core makes of about 1.9 s, in turn
ROUNDS = (49, 36, 24, 28, 21, 21, 13, 12)
WIDTH = 16


@pytest.mark.parametrize("size", ROUNDS)
def test_a_round_of_the_real_size_runs_width_trials_at_once_each_in_its_own_lane(size: int) -> None:
    """The first `width` waves of a round are inside their builds together (a barrier holds them there; it is released
    only by all of them arriving), no lane builds two waves at once, and every wave is done."""
    width = min(WIDTH, size)
    barrier = threading.Barrier(width)
    arrived, busy, peak, done = [], set(), [0], []
    lock = threading.Lock()

    def work(wave: int, lane: int) -> None:
        with lock:
            assert lane not in busy
            busy.add(lane)
            peak[0] = max(peak[0], len(busy))
            first = len(arrived) < width
            arrived.append(wave)
        if first:
            barrier.wait(timeout=30)  # a serial run never gets all `width` here: the barrier breaks
        with lock:
            busy.discard(lane)
            done.append(wave)

    process.together(list(range(size)), width, lambda n: n, work)
    assert sorted(done) == list(range(size)) and peak[0] == width


def test_a_failing_wave_is_raised_after_the_others_finished() -> None:
    done: list[int] = []

    def work(wave: int, lane: int) -> None:
        if wave == 1:
            raise ValueError("boom")
        done.append(wave)

    with pytest.raises(ValueError, match="boom"):
        process.together([0, 1, 2, 3], 2, lambda n: n, work)
    assert sorted(done) == [0, 2, 3]


def test_a_lane_tree_links_the_project_and_owns_the_files_a_trial_writes(tmp_path: Path) -> None:
    root, home = tmp_path / "root", tmp_path / "home"
    for path in ("SOURCE/A/a.CPP", "SOURCE/A/b.CPP", "SOURCE/B/c.CPP", "include/x.H", "build/us/a.o", "Makefile"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(path)
    typeunify._farm(root, home, ["include/x.H", "SOURCE/A/a.CPP"], skip=("build",))
    assert (home / "include").is_dir() and not (home / "include").is_symlink()
    assert not (home / "include/x.H").exists() and not (home / "SOURCE/A/a.CPP").exists()
    assert (home / "SOURCE/A/b.CPP").is_symlink() and (home / "SOURCE/A/b.CPP").read_text() == "SOURCE/A/b.CPP"
    assert (home / "SOURCE/B").is_symlink() and (home / "Makefile").is_symlink()
    assert not (home / "build").exists()
    (home / "include/x.H").write_text("trial")  # a write lands in the lane, never in the project
    assert (root / "include/x.H").read_text() == "include/x.H"
    typeunify._farm(root, home, ["SOURCE/B/c.CPP"], skip=("build",))  # rebuilt whole
    assert (home / "include").is_symlink() and (home / "include/x.H").read_text() == "include/x.H"


def _lanes(world: Culprits) -> Callable[[int, Collection[str]], tuple[Path, Seams]]:
    """A lane is a copy of the world's tree with its own counters, building like the world."""

    def lane(index: int, files: Collection[str]) -> tuple[Path, Seams]:
        home = world.root.parent / f"lane{index}"
        shutil.rmtree(home, ignore_errors=True)
        shutil.copytree(world.root, home)
        other = Culprits.__new__(Culprits)
        other.root, other.calls, other.culprits = home, [], world.culprits
        other.different, other.broken, other.snapshots = set(), set(), []
        return home, Seams(world.seams.units, world.seams.read, other.make)

    return lane


def test_lanes_hold_the_names_the_serial_search_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serial, _, _ = _wide_search(tmp_path / "serial", monkeypatch, 2)
    init = Culprits.__init__

    def lanes(self: Culprits, root: Path, culprits: dict[str, set[str]]) -> None:
        init(self, root, culprits)
        self.seams = replace(self.seams, lane=_lanes(self), width=4)

    monkeypatch.setattr(Culprits, "__init__", lanes)
    rounds: list[str] = []
    monkeypatch.setattr(typeunify.stages, "say", rounds.append)
    together, _, _ = _wide_search(tmp_path / "lanes", monkeypatch, 2)
    assert set(together["held"]) == set(serial["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    assert set(together["unified"]) == set(serial["unified"])
    assert any(r.startswith("unify.isolate round: ") for r in rounds)  # the passes did run in lanes
    assert together["objects"].keys() == serial["objects"].keys()


def test_idle_lanes_split_a_node_wide_so_the_search_needs_fewer_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Halving a few nodes leaves most of the width idle and costs a pass (a write and a make) per level: with the
    width free a node splits into as many parts as lanes, so 12 names with 3 culprits are searched in a pass or two."""
    init = Culprits.__init__

    def lanes(self: Culprits, root: Path, culprits: dict[str, set[str]]) -> None:
        init(self, root, culprits)
        self.seams = replace(self.seams, lane=_lanes(self), width=16)

    monkeypatch.setattr(Culprits, "__init__", lanes)
    rounds: list[str] = []
    monkeypatch.setattr(typeunify.stages, "say", rounds.append)
    wide, _, _ = _wide_search(tmp_path / "wide", monkeypatch, 2)
    assert set(wide["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    passes = [r for r in rounds if r.startswith("unify.isolate round: ")]
    assert 0 < len(passes) <= 2, passes


FILES = 1500  # headers and sources a wave1c rewrite set spans


@dataclass(frozen=True)
class _Held:
    name: str
    files: frozenset[str]
    header: str = ""


def test_a_lane_rewrites_only_the_files_that_differ_from_its_last_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a wrote every file of every rewrite in every wave: 1350 s of writes beside 1234 s of makes (run b)."""
    renders: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        typeunify, "render", lambda path, plans, src: renders.append((path, tuple(p.name for p in plans))) or path
    )
    monkeypatch.setattr(typeunify.store, "write", lambda path, data: path.parent.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(typeunify.stages, "stage", lambda name: contextlib.nullcontext())
    src = typeunify.Sources(lambda path: path)
    files = [f"SOURCE/{i // 50}/f{i}.CPP" for i in range(FILES)]
    plans = [_Held(f"n{i}", frozenset(files[i * 7 : i * 7 + 3])) for i in range(200)]
    lane: dict[str, frozenset[str]] = {}
    rendered: dict[tuple[str, frozenset[str]], bytes] = {}
    sizes = [size for size in ROUNDS for _ in range(1)]
    for wave, size in enumerate(sizes):
        held = plans[wave * 10 : wave * 10 + size // 10 + 1]
        typeunify._write(tmp_path, files, held, src, lane, rendered)
    # the first wave lays the lane out; each later one renders the files whose rewrites changed, never all
    assert len(renders) < FILES + 3 * 40 * len(sizes)
    count = len(renders)
    typeunify._write(tmp_path, files, held, src, lane, rendered)  # the same wave again: nothing differs
    assert len(renders) == count


def test_a_rewrite_set_is_rendered_once_for_all_the_lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renders: list[str] = []
    monkeypatch.setattr(typeunify, "render", lambda path, plans, src: renders.append(path) or path)
    monkeypatch.setattr(typeunify.store, "write", lambda path, data: path.parent.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(typeunify.stages, "stage", lambda name: contextlib.nullcontext())
    src = typeunify.Sources(lambda path: path)
    files = [f"SOURCE/f{i}.CPP" for i in range(FILES)]
    rendered: dict[tuple[str, frozenset[str]], bytes] = {}
    for lane_no in range(WIDTH):  # the first wave of each lane: a whole tree, rendered the first time only
        typeunify._write(tmp_path / str(lane_no), files, [], src, {}, rendered)
    assert len(renders) == FILES


def test_the_lanes_of_a_search_are_made_once_and_keep_their_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init = Culprits.__init__
    made: list[int] = []

    def lanes(self: Culprits, root: Path, culprits: dict[str, set[str]]) -> None:
        init(self, root, culprits)
        make = _lanes(self)

        def counted(index: int, files: Collection[str]) -> tuple[Path, Seams]:
            made.append(index)
            return make(index, files)

        self.seams = replace(self.seams, lane=counted, width=4)

    monkeypatch.setattr(Culprits, "__init__", lanes)
    rounds: list[str] = []
    monkeypatch.setattr(typeunify.stages, "say", rounds.append)
    found, _, _ = _wide_search(tmp_path / "lanes", monkeypatch, 2)
    assert set(found["held"]) == {"Aaa03", "Aaa07", "Aaa12"}
    assert len([r for r in rounds if r.startswith("unify.isolate round: ")]) >= 1
    assert sorted(made) == sorted(set(made))  # no lane number is made twice, however many passes ran


@pytest.mark.parametrize("size", ROUNDS)
def test_the_makes_of_a_round_share_one_jobserver_of_the_slot_pool(
    size: int, cfg: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slot pool is one holding per process with one feeder per jobserver, so `width` makes each taking their own
    raced on its lists (`list.remove(x): x not in list`, a make then refusing). The makes of a round take one
    jobserver around the round, whose env and fds every make is run with."""
    from abucache import slots

    from unbake import front

    entered, runs, lock = [], [], threading.Lock()
    real = slots.jobserver

    @contextlib.contextmanager
    def counted(work: int = 0, **kwargs: object):  # type: ignore[no-untyped-def]
        with real(work, **kwargs) as granted:  # type: ignore[arg-type]
            entered.append(granted)
            yield granted

    def fake_run(name: str, argv: object, cwd: Path, **kwargs: object) -> object:
        with lock:
            runs.append((kwargs["env"]["MAKEFLAGS"], kwargs["pass_fds"]))  # type: ignore[index]
        return NativeResult((name,), str(cwd), 0, None, b"", b"", 0.0, 0.0, 0, {})

    monkeypatch.setattr(slots, "jobserver", counted)
    monkeypatch.setattr(process, "run", fake_run)
    monkeypatch.setattr(process, "tool", lambda config, name: name)
    monkeypatch.setattr(process, "scratch", lambda root: root)
    monkeypatch.setattr(front, "cache", lambda host: None)
    width = min(WIDTH, size)
    process.together(
        list(range(size)),
        width,
        lambda n: n,
        lambda wave, lane: process.make(cfg, [f"o{wave}"], 4),
        jobs=4,  # type: ignore[arg-type]
    )
    assert len(entered) == 1 and len(runs) == size
    assert len(set(runs)) == 1 and "--jobserver-auth=" in runs[0][0]
    assert process._shared_jobserver.get() is None  # a make outside a round takes its own again
