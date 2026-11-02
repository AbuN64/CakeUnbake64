"""Unit-test hygiene, enforced on every test this suite runs (loaded by conftest).

A unit test mocks its seams: it never spawns a real build tool (make, a compiler, an assembler, a linker, binutils, a
build helper program) and it stays inside its declared seconds bound (`@pytest.mark.seconds(n)`, default
DEFAULT_SECONDS of CPU, this process and its children). A test that needs the real tool is marked
`@pytest.mark.integration("why")`: the integration set runs apart (`bin/test --integration`, part of ci/check) and is
exempt from both rules. A program a test wrote itself under its tmp directory (a stub) is not a real tool.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

DEFAULT_SECONDS = 1.0  # CPU seconds a unit test may use, setup and call; a test declares more with mark.seconds
WALL_FLOOR = 10.0  # a unit test blocked on something real (a lock, a child): wall seconds beyond this fail it too

# Real build tools by program name; a cross prefix (mips-linux-gnu-) is dropped first.
BUILD_TOOLS = frozenset(
    {
        *("make", "gmake", "cc", "c++", "gcc", "g++", "clang", "cc1", "cpp", "tcc"),
        *("as", "ld", "ar", "nm", "objcopy", "objdump", "readelf", "strip", "ranlib", "size"),
        *("abumas", "armips", "splat", "ido", "stamprec", "hdrkey"),
        "git",  # a repository is a tree build too: unit tests read fixture.Repo, the stand-in conftest routes to
    }
)
_CROSS = re.compile(r"^(?:[a-z0-9_]+-)+(?=(?:gcc|cpp|as|ld|ar|nm|objcopy|objdump|readelf|strip|ranlib|size)$)")
_HELPERS = Path(__file__).resolve().parents[1] / "src" / "unbake" / "resources"  # the build's own helper programs

_CENSUS = os.environ.get("UNIT_HYGIENE_CENSUS", "")  # a file prefix: record every breach there instead of failing
_census: list[str] = []
_active: list[pytest.Item] = []
_spawned: dict[str, list[str]] = {}
_real_popen_init = subprocess.Popen.__init__


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--integration", action="store_true", help="run only the integration set (real tools)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration(why): runs real tools; apart from the unit suite, says why")
    bound = f"seconds(n): this unit test's CPU bound in seconds (default {DEFAULT_SECONDS:g})"
    config.addinivalue_line("markers", bound)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    want = config.getoption("--integration")
    keep, drop = [], []
    for item in items:
        marked = item.get_closest_marker("integration")
        if marked is not None and not (marked.args and str(marked.args[0]).strip()):
            raise pytest.UsageError(f"{item.nodeid}: mark.integration needs its reason")
        (keep if (marked is not None) == want else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def _program(args: object, kwargs: dict) -> tuple[str, list[str]]:
    if isinstance(args, (str, bytes, os.PathLike)):
        argv = [os.fsdecode(args)]
        if kwargs.get("shell"):
            argv = ["/bin/sh", "-c", argv[0]]
    else:
        argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args]  # type: ignore[union-attr]
    first = kwargs.get("executable") or (argv[0] if argv else "")
    first = os.fsdecode(first)
    if os.sep not in first:
        env = kwargs.get("env") or os.environ
        first = shutil.which(first, path=env.get("PATH")) or first
    return first, argv


def _tool(name: str) -> str:
    """A program name less its cross prefix and version suffix: mips-linux-gnu-as is as, x86_64-linux-gnu-gcc-13 gcc."""
    return _CROSS.sub("", re.sub(r"-[0-9][0-9.]*$", "", name))


def refusal(args: object, kwargs: dict, own: Path | None) -> str:
    """Why spawning `args` is a real build tool for a unit test, or '' when it is not one."""
    first, argv = _program(args, kwargs)
    path = Path(first)
    with contextlib.suppress(OSError):
        path = path.resolve()
    if own is not None and path.is_relative_to(own):
        return ""  # a stub this test wrote
    if any(_tool(n) in BUILD_TOOLS for n in (Path(first).name, path.name)):
        return f"the real build tool {first}"
    if Path(sys.executable).name in (path.name, Path(first).name) or path.name.startswith("python"):
        for arg in argv[1:]:
            if not arg.startswith("-") and Path(arg).suffix == ".py":
                script = Path(arg).resolve()
                if script.is_relative_to(_HELPERS):
                    return f"the build helper program {script.name} in a process of its own"
                break
    if path.name in ("sh", "bash", "dash") and len(argv) > 2 and argv[1] == "-c":
        commands = [c.split() for c in re.split(r"[;&|\n(`]+", argv[2])]  # each command's program: its first word
        tool = next((c[0] for c in commands if c and _tool(Path(c[0].strip("'\"")).name) in BUILD_TOOLS), None)
        if tool:
            return f"the real build tool {tool} through a shell"
    return ""


def _guarded_init(self: subprocess.Popen, args: object, *rest: object, **kwargs: object) -> None:
    item = _active[-1] if _active else None
    if item is not None and item.get_closest_marker("integration") is None:
        own = getattr(item, "_hygiene_tmp", None)
        why = refusal(args, kwargs, own)
        if why:
            _spawned.setdefault(item.nodeid, []).append(why)
            if _CENSUS:
                _census.append(f"spawn\t{item.nodeid}\t{why}")
                return _real_popen_init(self, args, *rest, **kwargs)  # type: ignore[arg-type]
            raise RefusedSpawn(f"a unit test spawned {why}: mock its seam, or mark the test integration with why")
    _real_popen_init(self, args, *rest, **kwargs)  # type: ignore[arg-type]


class RefusedSpawn(OSError):
    """Raised in a unit test's process start; the test fails on it even when the code under test catches it."""


subprocess.Popen.__init__ = _guarded_init  # type: ignore[method-assign]


_slept = [0.0]
_real_sleep = time.sleep


def _sleep(seconds: float) -> None:
    """time.sleep, counted: a unit test's sleeps count against its bound like CPU does."""
    if _active:
        _slept[0] += seconds
    _real_sleep(seconds)


time.sleep = _sleep


def _cpu() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system + _slept[0]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    factory = getattr(item.config, "_tmp_path_factory", None)
    if factory is not None:
        base = factory.getbasetemp()
        item._hygiene_tmp = (base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base).resolve()  # type: ignore[attr-defined]
    _active.append(item)
    item._hygiene_start = (_cpu(), time.monotonic())  # type: ignore[attr-defined]
    try:
        yield
    finally:
        _active.pop()
        _spawned.pop(item.nodeid, None)


def bound(item: pytest.Item) -> float:
    marked = item.get_closest_marker("seconds")
    return float(marked.args[0]) if marked and marked.args else DEFAULT_SECONDS


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or item.get_closest_marker("integration") is not None or not report.passed:
        return
    spawned = _spawned.get(item.nodeid)
    cpu0, wall0 = item._hygiene_start  # type: ignore[attr-defined]
    cpu, wall, limit = _cpu() - cpu0, time.monotonic() - wall0, bound(item)
    why = ""
    if spawned:
        why = f"unit hygiene: the test spawned {spawned[0]} (caught by the code under test)"
    elif cpu > limit:
        why = f"unit hygiene: {cpu:.2f} s (CPU and sleeps) over the test's bound of {limit:g} s: mock the slow seam"
    elif wall > max(WALL_FLOOR, 4 * limit):
        why = (
            f"unit hygiene: {wall:.2f} s wall for a test bound to {limit:g} s CPU: it sleeps or waits on something real"
        )
    if why and _CENSUS:
        _census.append(f"bound\t{item.nodeid}\t{why}")
    elif why:
        report.outcome = "failed"
        report.longrepr = why


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    """The width the suite ran at, for the summary line's seconds to be read against."""
    width = config.getoption("numprocesses", default=None) or 1
    kind = "integration set" if config.getoption("--integration") else "unit suite"
    terminalreporter.write_line(f"{kind} at width {width}")


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _CENSUS and _census:
        name = f"{_CENSUS}.{os.environ.get('PYTEST_XDIST_WORKER', 'main')}.tsv"
        Path(name).write_text("".join(line + "\n" for line in _census))
