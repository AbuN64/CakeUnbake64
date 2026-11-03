"""The shell runner isolates bulky fixtures on disk while keeping the job's socket directory."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration("exercise the real shell runner with a stand-in pytest process")
RUNNER = Path(__file__).resolve().parents[1] / "bin" / "test"


def runner(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "bin").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    shutil.copy2(RUNNER, root / "bin/test")
    (root / "pyproject.toml").write_text("")
    (root / ".venv/pyvenv.cfg").write_text(f"command = python3 -m venv {root}/.venv\n")
    (root / ".venv/.pyproject.sha256").write_text(hashlib.sha256(b"").hexdigest() + "  pyproject.toml\n")
    python = root / ".venv/bin/python"
    python.write_text(
        "#!/usr/bin/env bash\n"
        '[[ "$1" == -c ]] && exit 1\n'
        '[[ "$1 $2" == "-m pytest" ]] || exit 99\n'
        "shift 2\n"
        'while [[ "$1" != --basetemp ]]; do shift; done\n'
        'touch "$2/fixture"\n'
        'printf "%s\\n" "$2"\n'
        "read -r result\n"
        'exit "$result"\n'
    )
    python.chmod(0o755)
    return root


@pytest.mark.parametrize("result", [0, 7])
def test_runner_removes_its_disk_fixtures_on_success_and_failure(tmp_path: Path, result: int) -> None:
    root = runner(tmp_path)
    answer = subprocess.run(
        [root / "bin/test"], input=f"{result}\n", text=True, capture_output=True, env={**os.environ, "JOBS": "1"}
    )
    assert answer.returncode == result, answer.stderr
    base = Path(answer.stdout.strip())
    assert base.parent == root / ".tmp"
    assert not base.exists()


def test_concurrent_runners_own_distinct_disk_fixtures(tmp_path: Path) -> None:
    root = runner(tmp_path)
    processes = [
        subprocess.Popen(
            [root / "bin/test"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "JOBS": "1"},
        )
        for _ in range(2)
    ]
    try:
        bases = [Path(process.stdout.readline().strip()) for process in processes]  # type: ignore[union-attr]
        assert bases[0] != bases[1]
        assert all((base / "fixture").exists() for base in bases)
    finally:
        for process in processes:
            out, err = process.communicate("0\n")
            assert process.returncode == 0, (out, err)
    assert all(not base.exists() for base in bases)
