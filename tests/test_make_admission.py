"""Freshly emitted make delegates to AbuCache64; real make, stub build recipes."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from abucache import slots

from unbake import build, repo

pytestmark = pytest.mark.integration("generated make and the slot jobserver are the integration under test")


def emit(cfg, monkeypatch):
    root = cfg.project.root
    # The linker/ROM documentation are unrelated seams; emit the real make program through sync.
    monkeypatch.setattr(repo, "_generated", lambda config, current: {"Makefile": build.makefile(config)})
    (root / "README.md").unlink(missing_ok=True)
    repo.sync(cfg, {"makefile": True})
    probe = root / "probe.py"
    probe.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from abucache import slots\n"
        "pool = Path(os.environ['XDG_RUNTIME_DIR']) / slots.POOL\n"
        "print(json.dumps({'width': os.environ.get(slots.CORES_ENV), "
        "'flags': os.environ.get('MAKEFLAGS'), 'level': os.environ.get('MAKELEVEL'), "
        "'held': sum(slots._busy(p) for p in pool.glob('slot-*.lock'))}))\n"
    )
    text = (root / "Makefile").read_text()
    text = text.replace(
        "endif # admitted make",
        f".PHONY: probe\nprobe:\n\t@{shlex.quote(sys.executable)} {shlex.quote(str(probe))}\nendif # admitted make",
    )
    (root / "Makefile").write_text(text)
    return root


def run(root, *args, env=None, pass_fds=()):
    return subprocess.run(
        ["make", "--no-print-directory", "CHECK=tree", "probe", *args],
        cwd=root,
        env=env,
        pass_fds=pass_fds,
        text=True,
        capture_output=True,
    )


def observation(done):
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(next(line for line in done.stdout.splitlines() if line.startswith("{")))


def test_generated_make_uses_grant_not_jobs_and_releases_on_exit(cfg, monkeypatch):
    root = emit(cfg, monkeypatch)
    monkeypatch.setenv("JOBS", "99")
    seen = observation(run(root, "-j99"))
    assert seen["width"] == "2"
    assert "-j2" in seen["flags"] and "-j99" not in seen["flags"]
    assert seen["held"] == 2
    assert seen["level"] == "1"  # the admission delegate must be transparent to make recursion
    pool = Path(os.environ["XDG_RUNTIME_DIR"]) / slots.POOL
    assert not any(slots._busy(p) for p in pool.glob("slot-*.lock"))


def test_generated_make_respects_inherited_jobserver(cfg, monkeypatch):
    root = emit(cfg, monkeypatch)
    read, write = os.pipe()
    os.write(write, b"++")
    try:
        flags = f"-j3 --jobserver-auth={read},{write}"
        monkeypatch.setenv("MAKEFLAGS", flags)
        seen = observation(run(root, pass_fds=(read, write)))
        assert f"--jobserver-auth={read},{write}" in seen["flags"]
        assert "-j3" in seen["flags"]
        assert seen["held"] == 0  # the parent owns admission; generated make never acquires a second grant
        assert os.read(read, 2) == b"++"
    finally:
        os.close(read)
        os.close(write)


def test_generated_make_refuses_without_admission(cfg, monkeypatch):
    root = emit(cfg, monkeypatch)
    monkeypatch.setenv("ABU_RUNTIME", str(root / "missing-runtime.toml"))
    monkeypatch.setenv("JOBS", "99")
    done = run(root, "-j99")
    assert done.returncode != 0
    assert "make admission refused" in done.stderr
    assert not any(line.startswith("{") for line in done.stdout.splitlines())


def test_generated_make_respects_inherited_width_over_raw_j(cfg, monkeypatch):
    root = emit(cfg, monkeypatch)
    monkeypatch.setenv(slots.CORES_ENV, "2")
    seen = observation(run(root, "-j99"))
    assert seen["width"] == "2"
    assert "-j2" in seen["flags"] and "-j99" not in seen["flags"]
