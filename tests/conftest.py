"""Shared pytest fixtures. The fake toolchain row replaces toolchains.toml only; every other resource loads for real."""

from __future__ import annotations

import copy
import itertools
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fixture
import pytest
from abucache import memo, pool, stages
from abucache import store as cache_store
from abucache.proc import Completed
from abucache.store import forget

from unbake import adapters, effort, hostruntime, process
from unbake import config as configuration
from unbake.contracts import Config, NativeResult

pytest_plugins = ["hygiene", "pytester"]  # unit tests mock their seams and keep their seconds bound

TOOLCHAIN_ROW = {
    "family": "gcc",
    "cc": "cc",
    "as_host": "mips_as",
    "cflags": ["-O2"],
    "supported_options": ["-O1", "-O2"],
    "splat": "splat",
    "emits_asm": False,
    "preserves_padding": True,
    "never_emits": ["mfc0", "mtc0", "dmfc0", "dmtc0", "cache", "sync", "eret", "tlbp", "tlbr", "tlbwi", "tlbwr"],
    "fpu_control_with": ["cvt.w.s", "cvt.w.d", "trunc.w.s", "trunc.w.d"],
    "refused_warnings": ["makes pointer from integer without a cast", "from incompatible pointer type"],
    "paired": [],
    "preprocessor_options": ["-D", "-U", "-I"],
    "small_data": [8],
    "isa": [3],
    "pins": {},
    "downloads": [],
    "cppflags": [],
    "preprocess": ["{cpp}", "-E", "{cppflags}", "{defines}", "{includes}", "{source}"],
    "compile": ["{cc}", "{codegen}", "-c", "{source}", "-o", "{out}"],
}


def _ido_row() -> dict[str, Any]:
    """The shipped ido-7.1 row without pins or downloads, so tests need no installed toolchain."""
    row = dict(configuration.load_resource("toolchains.toml")["toolchain"]["ido-7.1"])
    return {**row, "pins": {}, "downloads": []}


@pytest.fixture
def toolchains(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Inject the toolchain rows gcc-test and ido-7.1 (no pins, no downloads) into config.load_resource."""
    real = configuration.load_resource
    # Fresh copies per test: tests may edit rows without leaking into later tests.
    document = {"schema": 1, "toolchain": {"gcc-test": copy.deepcopy(TOOLCHAIN_ROW), "ido-7.1": _ido_row()}}

    def load_resource(name: str) -> Any:
        return document if name == "toolchains.toml" else real(name)

    monkeypatch.setattr(configuration, "load_resource", load_resource)
    return document


@pytest.fixture
def host_file(tmp_path: Path) -> Path:
    return fixture.host(tmp_path)


@pytest.fixture
def cfg(request: pytest.FixtureRequest, tmp_path: Path, toolchains: dict[str, Any]) -> Config:
    """A two-version schema-4 config (two segments, three modules); in a real git repository for an integration
    test, else in the stand-in one (fixture.Repo)."""
    return fixture.config(tmp_path, git=request.node.get_closest_marker("integration") is not None)


@pytest.fixture(scope="session")
def _process_state_folders(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Folders for each test's process state, numbered under one session folder: mktemp per folder rescans its numbered
    siblings, so four of them per test grew with the suite."""
    root = tmp_path_factory.mktemp("state")

    def folders() -> Iterator[Path]:
        for number in itertools.count():
            folder = root / str(number)
            for name in ("run", "cwd", "cache", "spill"):
                (folder / name).mkdir(parents=True)
            yield folder

    return folders()


@pytest.fixture
def _process_state(_process_state_folders: Iterator[Path]) -> Path:
    return next(_process_state_folders)


@pytest.fixture(autouse=True)
def _fresh_module_state(monkeypatch, _process_state):
    """Every test starts with empty process-wide caches, so xdist workers never share state between tests, and with
    worker slots of its own: tests never touch the host's real slot and command locks."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(_process_state / "run"))
    monkeypatch.delenv("UNBAKE_CORES", raising=False)  # a test runner's holder is not these tests' holder
    forget()
    memo._clear()
    stages._listeners[:] = [effort._track]  # the hold beat reads the open stage from the one tracker
    monkeypatch.chdir(_process_state / "cwd")  # a command's stage log goes under its working directory
    pool._drop_executor()
    host = SimpleNamespace(  # pool.map without a bound config still finds a runtime
        cores=2,
        slots=2,
        memory_worker_bytes=1 << 28,
        memory_parent_bytes=1 << 28,
        cache_dir=_process_state / "cache",
        cache_max_bytes=1 << 30,
    )
    runtime = hostruntime.runtime(host)  # type: ignore[arg-type]
    pool.configure(runtime, spill_dir=_process_state / "spill", identity_package="unbake")
    yield


@pytest.fixture(autouse=True)
def _fixture_seams(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fixture project's programs and repository answer in process: process.run of a program fixture.host wrote
    (the fake cpp, the copies of true) runs its fixture.FAKES stand-in, and the git queries of process over a project
    fixture.project committed read its fixture.Repo. Everything else reaches the real function."""
    run, queries = process.run, {n: getattr(process, n) for n in ("tracked", "tree_id", "dirty", "changed", "blob")}

    def fake_run(name: str, argv: Sequence[str | Path], cwd: Path, **kw: Any) -> NativeResult:
        stand_in = fixture.fake(argv[0]) if argv else None
        repo = fixture.repo(cwd) if argv and Path(argv[0]).name == "git" else None
        if repo is not None:
            stand_in = repo.git
        if stand_in is None:
            return run(name, argv, cwd, **kw)
        start = time.perf_counter_ns()
        code, out, err = stand_in([str(a) for a in argv], Path(cwd), kw.get("stdin", b""))
        if kw.get("stdout_path") is not None:
            Path(kw["stdout_path"]).write_bytes(out)
            out = b""
        wall = (time.perf_counter_ns() - start) / 1e9
        result = NativeResult(
            tuple(map(str, argv)), str(cwd), code, None, out, err, wall, wall, 0, dict(kw.get("outputs", {}))
        )
        stages.record_native(name, Completed(result.argv, result.cwd, code, None, b"", b"", wall, wall, 0), start)
        return result

    def routed(query: str, answer: Callable[..., Any]) -> Callable[..., Any]:
        def ask(root: Path, *args: str) -> Any:
            repo = fixture.repo(root)
            return queries[query](root, *args) if repo is None else answer(repo, *args)

        return ask

    def blob(repo: fixture.Repo, rev: str, path: str) -> bytes | None:
        return repo.files.get(path) if repo.head(rev) else None

    monkeypatch.setattr(process, "run", fake_run)
    monkeypatch.setattr(process, "tracked", routed("tracked", lambda repo: set(repo.files)))
    monkeypatch.setattr(process, "tree_id", routed("tree_id", lambda repo, rev: repo.tree if repo.head(rev) else ""))
    monkeypatch.setattr(process, "dirty", routed("dirty", lambda repo: bool(repo.changed())))
    monkeypatch.setattr(
        process, "changed", routed("changed", lambda repo, rev: repo.changed() if repo.head(rev) else None)
    )
    monkeypatch.setattr(process, "blob", routed("blob", blob))
    queries["cache_tree"] = cache_store._tree  # the cache's manifest names the root's HEAD tree, read with git
    monkeypatch.setattr(cache_store, "_tree", routed("cache_tree", lambda repo: repo.tree))
    yield
    fixture.FAKES.clear()
    fixture.REPOS.clear()


@pytest.fixture(autouse=True)
def _pinned_abucache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup checks the installed abucache against the unbake requirement the venv recorded at install time: a test
    venv installed before a pin bump would fail every setup test. Tests judge the code, never the runner's install;
    test_adapters checks the pin reading itself on a canned requirement."""
    monkeypatch.setattr(adapters, "_pin", adapters._installed)


@pytest.fixture
def plain_roms(monkeypatch: pytest.MonkeyPatch) -> None:
    """For a stub project: a ROM is its plain path, unverified, and its meta is the row's (test_config is real)."""
    monkeypatch.setattr(configuration, "rom", lambda project, vid: project.root / "roms" / f"baserom.{vid}.z64")
    monkeypatch.setattr(configuration, "rom_meta", lambda project, vid: project.version[vid].meta)
