"""unbake's seat on the one timing path (abucache.stages): the command's niceness, its hold beat and the binding of the
host config to the stage tree. The tree itself (spans, records, counters, pool folds, the stage log) is abucache's."""

from __future__ import annotations

import os
import resource
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psutil
from abucache import stages

from unbake.contracts import Config, Finding, Json, Refusal

__plumbing__ = True  # contracts.code_identity names this code and never follows it: it shapes no result

NICE = 15  # the niceness of every command but the `--priority` holder; UNBAKE_NICE overrides, 0 keeps
priority = False  # this command is the job every tree waits on (the ship/land lock holder): `unbake --priority`
HOLD_BEAT = 300.0  # a command still running after this many seconds names its stage and CPU, then again every beat
_live: list[tuple[str, ...]] = []  # the paths of the stages open now (outermost first), for the hold beat


def _finding(found: Any) -> dict[str, Any]:
    return found if isinstance(found, dict) else asdict(found)


stages.finding_to_dict = _finding
stages.register_refusal(Refusal)


def _track(event: str, body: Json) -> None:
    path = tuple(body.get("path", ()))
    if event == "open":
        _live.append(path)
    elif event == "close" and path in _live:
        _live.remove(path)


stages.listen(_track)


def set_priority(on: bool) -> None:
    global priority
    priority = on


def yield_cpu() -> int:
    """One rule for the host: only the job every tree waits on (the ship/land lock holder, `unbake --priority`) runs at
    normal priority; any other command (and its workers, compilers and permuters, which inherit it) runs nice, so a
    crowd of lane commands never slows the lock holder. Returns the niceness now (never lowers an existing one)."""
    want = int(os.environ.get("UNBAKE_NICE", NICE))
    if priority or want <= 0:
        return os.nice(0)
    try:
        now = os.nice(0)
        return os.nice(want - now) if now < want else now
    except OSError:
        return -1


def _tree_cpu() -> float:
    """CPU seconds of this process, its finished children and everything still running below it."""
    try:
        me = psutil.Process(os.getpid())
        total = sum(me.cpu_times()[:4])
        for child in me.children(recursive=True):
            try:
                total += sum(child.cpu_times()[:2])
            except psutil.Error:
                continue
        return total
    except psutil.Error:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime


@contextmanager
def _hold_beat(name: str, beat: float) -> Iterator[None]:
    """Never a silent hold: every `beat` seconds the command runs, one `say` line gives its wall, the CPU of its whole
    process tree and the innermost open stage, so whoever waits on it (a ship's queue) sees which stage holds them."""
    stop, began, cpu0 = threading.Event(), time.perf_counter(), _tree_cpu()

    def run() -> None:
        while not stop.wait(beat):
            at = " > ".join(_live[-1]) if _live else name
            stages.say(
                f"hold: {name} runs {time.perf_counter() - began:.0f} s, {_tree_cpu() - cpu0:.0f} CPU-s, now in {at}"
            )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()


def sink(project: Path) -> Path:
    """The stage log of a project: its rows, and the walls of the previous command beside it."""
    return project / ".unbake" / "stages.jsonl"


@contextmanager
def command(name: str, argv: Sequence[str], project: Path) -> Iterator[None]:
    """The span of one command of `project`: every stage opened inside it is recorded under it."""
    _live.clear()
    yield_cpu()
    with _hold_beat(name, HOLD_BEAT), stages.command("unbake", name, argv, sink(project)):
        yield None


def bind(config: Config) -> None:
    """Gives the stage tree the host's budgets, memory limits and the project the wait log's lines name."""
    host = config.host
    keys = ("resources.memory_parent_bytes", "resources.memory_worker_bytes")
    stages.configure(
        budgets=stages.Budgets(host.serial_seconds, host.serial_cores, host.pool_fill, host.pool_fanout),
        budget_origins={k: v for k, v in host.origins.items() if k.startswith("budgets.")},
        memory_limits={k: host.origins[k].file for k in keys},
        memory_limit_values=dict(zip(keys, (host.memory_parent_bytes, host.memory_worker_bytes), strict=True)),
        parent=host.memory_parent_bytes,
        package="unbake",
        project=str(config.project.root),
    )


__all__ = ["Finding", "bind", "command", "sink", "yield_cpu"]
