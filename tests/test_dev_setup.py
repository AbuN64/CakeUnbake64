"""Unbake names abucache bare and its own setup installs the commit abucache has landed, with no pin."""

import os
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LANDED = "43bb0bee68dafc54a2ea0f1dad156806c6cf20ed"


def _tool(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_pyproject_names_abucache_bare():
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "abucache" in pyproject["project"]["dependencies"]
    assert not any(r.startswith("abucache") and r != "abucache" for r in pyproject["project"]["dependencies"])


def test_setup_installs_the_landed_abucache_commit_before_the_project(tmp_path):
    """git, python3 and pip are canned: the log shows which requirement each install was given, in order."""
    checkout, fake = tmp_path / "checkout", tmp_path / "fake"
    (checkout / "bin").mkdir(parents=True)
    fake.mkdir()
    for script in ("dev-setup", "abucache-requirement"):
        shutil.copy(_ROOT / "bin" / script, checkout / "bin" / script)
    shutil.copy(_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    log = tmp_path / "log"
    _tool(fake / "git", f'[[ "$1" == ls-remote ]] || exit 1\nprintf "{_LANDED}\\tHEAD\\n"\n')
    _tool(fake / "python3", 'mkdir -p .venv/bin\ncp "$FAKE_PIP" .venv/bin/pip\n')
    _tool(tmp_path / "pip", f'echo "$@" >> "{log}"\n')
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE_PIP": str(tmp_path / "pip")}
    subprocess.run([checkout / "bin" / "dev-setup"], check=True, env=env, capture_output=True)
    first, second = log.read_text().splitlines()
    assert first == f"install abucache @ git+https://github.com/AbuN64/cache-64@{_LANDED}"
    assert second == "install -e .[dev]"


def test_one_resolver_owns_the_abucache_url_and_the_landed_lookup():
    """dev-setup, the rig deploy build and ci call bin/abucache-requirement; none keeps a second resolver."""
    for path in ("bin/dev-setup", "ci/deploy_rig.py", ".github/workflows/ci.yml", "ci/abucache-pin-proof"):
        text = (_ROOT / path).read_text(encoding="utf-8")
        assert "abucache-requirement" in text, path
        assert "ls-remote" not in text and "cache-64@" not in text, path
