"""Shared pytest fixtures. The fake toolchain row replaces toolchains.toml only; every other resource loads for real."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fixture
import pytest
from abucache import memo, pool, stages
from abucache.store import forget

from unbake import adapters, effort, hostruntime
from unbake import config as configuration
from unbake.contracts import Config

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
def cfg(tmp_path: Path, toolchains: dict[str, Any]) -> Config:
    """A two-version schema-4 config (two segments, three modules)."""
    return fixture.config(tmp_path)


@pytest.fixture(autouse=True)
def _fresh_module_state(monkeypatch, tmp_path_factory):
    """Every test starts with empty process-wide caches, so xdist workers never share state between tests, and with
    worker slots of its own: tests never touch the host's real slot and command locks."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("run")))
    monkeypatch.delenv("UNBAKE_CORES", raising=False)  # a test runner's holder is not these tests' holder
    forget()
    memo._clear()
    stages._listeners[:] = [effort._track]  # the hold beat reads the open stage from the one tracker
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))  # a command's stage log goes under its working directory
    pool._drop_executor()
    host = SimpleNamespace(  # pool.map without a bound config still finds a runtime
        cores=2,
        slots=2,
        memory_worker_bytes=1 << 28,
        memory_parent_bytes=1 << 28,
        cache_dir=tmp_path_factory.mktemp("cache"),
        cache_max_bytes=1 << 30,
    )
    runtime = hostruntime.runtime(host)  # type: ignore[arg-type]
    pool.configure(runtime, spill_dir=tmp_path_factory.mktemp("spill"), identity_package="unbake")
    yield


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
