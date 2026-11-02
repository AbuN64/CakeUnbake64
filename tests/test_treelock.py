"""treelock: readers share the tree, a writer holds it alone, and a waiting reader says so instead of failing.

Another process's hold is a hold this process takes through open files of its own (a flock belongs to the open file, so
the next open waits on it as on another process's), with the mark a holder gives its children taken off again. Time is
a Clock whose pause lets the other hold go once it has run its seconds: no test sleeps or starts a process."""

from __future__ import annotations

import fcntl
import os
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from unbake import treelock


class Other:
    """Another holder of the tree lock of `root`, exclusive or shared, until release()."""

    def __init__(self, root: Path, exclusive: bool, who: str = "") -> None:
        self.hold: AbstractContextManager[float] = treelock.hold(root, exclusive=exclusive, who=who, say=lambda _: None)
        self.hold.__enter__()
        os.environ.pop(treelock.ENV)  # its mark is for its own children, not for the holds this test takes
        self.held = True

    def release(self) -> None:
        if self.held:
            self.held = False
            self.hold.__exit__(None, None, None)


class Clock:
    """The time a hold waits in: each pause advances it; `others` let go when it reaches their seconds."""

    def __init__(self, **others: tuple[Other, float]) -> None:
        self.now, self.others, self.pauses = 0.0, list(others.values()), 0

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += seconds
        self.pauses += 1
        for other, at in self.others:
            if self.now >= at:
                other.release()


def test_readers_share_and_a_reader_waits_out_a_writer(tmp_path: Path) -> None:
    said: list[str] = []
    other = Other(tmp_path, exclusive=False)
    clock = Clock()
    with treelock.hold(tmp_path, clock=clock, pause=clock.pause) as waited:  # a shared hold beside a shared one
        assert waited < 0.2 and clock.pauses == 0
    other.release()
    clock = Clock(writer=(Other(tmp_path, exclusive=True), 1.0))
    with treelock.hold(tmp_path, who="try f", say=said.append, clock=clock, pause=clock.pause) as waited:
        assert waited >= 0.5 and clock() >= 0.5
    assert said and said[0].startswith("waiting for ")


def test_a_measurer_never_waits_for_a_writer_and_names_it(tmp_path: Path) -> None:
    said: list[str] = []
    writer = Other(tmp_path, exclusive=True)
    clock = Clock()
    treelock.inflight.clear()
    with treelock.hold(tmp_path, who="probe", say=said.append, wait=False, clock=clock, pause=clock.pause) as waited:
        assert waited == 0.0 and clock() < 0.5 and clock.pauses == 0
    writer.release()
    assert len(treelock.inflight) == 1 and "pid " in treelock.inflight[0] and "not waiting" in treelock.inflight[0]
    assert said and "writer" in said[0]
    treelock.inflight.clear()
    with treelock.hold(tmp_path, who="probe", wait=False):  # no writer: an ordinary shared hold
        pass
    assert not treelock.inflight


def test_a_waiting_reader_names_the_writer_pid_and_start(tmp_path: Path) -> None:
    said: list[str] = []
    clock = Clock(writer=(Other(tmp_path, exclusive=True), 0.6))
    with treelock.hold(tmp_path, who="compare", say=said.append, clock=clock, pause=clock.pause):
        pass
    assert said and " since " in said[0] and f"(pid {os.getpid()})" in said[0]


def test_a_holder_marks_its_children_so_they_run_inside_the_hold(tmp_path: Path) -> None:
    with treelock.hold(tmp_path, exclusive=True), treelock.hold(tmp_path) as waited:  # would deadlock without the mark
        assert waited == 0.0
    assert treelock.ENV not in __import__("os").environ


def test_a_stream_of_readers_never_starves_a_writer_and_the_writer_bumps_the_epoch(tmp_path: Path) -> None:
    """Readers back to back, overlapping: without a writer's announcement the lock is never free. Two readers are
    inside; each pause of the writer one of them leaves (a reader holds about 0.4 s) and a new one comes, and the new
    one finds the announcement and queues behind it instead of entering."""
    inside = [Other(tmp_path, exclusive=False), Other(tmp_path, exclusive=False)]
    queued: list[bool] = []
    clock = Clock()

    def pause(seconds: float) -> None:
        clock.pause(seconds)
        if clock.pauses % 2 == 0 and inside:  # every 0.4 s a reader inside finishes
            inside.pop(0).release()
        arriving = os.open(tmp_path / ".unbake/tree.writer", os.O_RDWR)
        try:
            fcntl.flock(arriving, fcntl.LOCK_SH | fcntl.LOCK_NB)  # a reader's first step: past the announcement
            queued.append(False)
        except BlockingIOError:
            queued.append(True)
        finally:
            os.close(arriving)

    before = treelock.epoch(tmp_path)
    with treelock.hold(tmp_path, exclusive=True, who="ship", say=lambda text: None, clock=clock, pause=pause) as waited:
        assert waited < 1.5  # within about one reader's hold, not when the stream ends
    assert queued and all(queued)  # every reader that came while the writer waited queued behind it
    assert treelock.epoch(tmp_path) == before + 1


def test_a_reader_behind_a_writer_names_it(tmp_path: Path) -> None:
    said: list[str] = []
    clock = Clock(writer=(Other(tmp_path, exclusive=True), 1.0))
    with treelock.hold(tmp_path, say=said.append, clock=clock, pause=clock.pause):
        pass
    assert said and "waiting for" in said[0]


def test_the_tree_lock_is_refused_while_holding_the_journal_lock(tmp_path: Path) -> None:
    from unbake import journal
    from unbake.contracts import Refusal

    writer = Other(tmp_path, exclusive=True)
    journal._HELD.add(tmp_path)
    try:
        with pytest.raises(Refusal) as refused, treelock.hold(tmp_path, exclusive=True, who="landing"):
            pass
    finally:
        journal._HELD.discard(tmp_path)
        writer.release()
    finding = refused.value.findings[0]
    assert finding.key == "lock.order" and f"pid {os.getpid()}" in finding.reason


def test_a_writer_named_with_spaces_does_not_crash_a_waiting_reader(tmp_path: Path) -> None:
    (tmp_path / ".unbake").mkdir()
    (tmp_path / ".unbake/tree.writer").write_text("land func_8021AF6C_de 1792681126 pid 60708")
    said: list[str] = []
    clock = Clock(writer=(Other(tmp_path, exclusive=True, who="land func_8021AF6C_de"), 0.6))
    with treelock.hold(tmp_path, who="map", say=said.append, clock=clock, pause=clock.pause):
        pass
    assert said and "land func_8021AF6C_de since" in said[0]  # it waited and said so, not ValueError


def test_read_only_commands_never_wait_for_a_ship():
    from unbake import cli

    assert {"score", "probe", "map", "symbols"} <= cli._MEASURERS <= cli._READERS
