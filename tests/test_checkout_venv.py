"""Public script regressions: fixture environments never import product code or use the network."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, text: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def stamp(root: Path) -> None:
    digest = hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest()
    (root / ".venv/.pyproject.sha256").write_text(f"{digest}  pyproject.toml\n")


def fake_python(root: Path) -> None:
    executable(
        root / ".venv/bin/python",
        '#!/bin/bash\nprintf "python" >> calls\nprintf " <%s>" "$@" >> calls\nprintf "\\n" >> calls\n'
        'if [[ "$1" == -c ]]; then exit 1; fi\n'
        '[[ "$1" == -m && "$2" == pytest ]] || exit 93\n',
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "bin").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    for script in ("test", "dev-setup"):
        shutil.copy2(ROOT / "bin" / script, root / "bin" / script)
    (root / "pyproject.toml").write_text("[project]\ndependencies = []\n[project.optional-dependencies]\ndev = []\n")
    (root / ".venv/pyvenv.cfg").write_text(f"command = /usr/bin/python3 -m venv {root}/.venv\n")
    fake_python(root)
    executable(root / ".venv/bin/pip", "#!/bin/bash\necho pip >> calls\n")
    stamp(root)
    return root


def invoke(root: Path, *args: str, script: str = "test", path: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "ABU_TESTS_LOCKED": "1", "JOBS": "1", "UNBAKE_CORES": "1"}
    if path is not None:
        env["PATH"] = f"{path}:{env['PATH']}"
    return subprocess.run([str(root / "bin" / script), *args], cwd=root, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize(
    "defect", ["moved", "missing-cfg", "unreadable-cfg", "missing-target", "missing-command", "missing-python"]
)
def test_refuses_before_environment_execution(checkout: Path, defect: str, changed: bool) -> None:
    cfg = checkout / ".venv/pyvenv.cfg"
    if defect == "moved":
        cfg.write_text(f"command = /usr/bin/python3 -m venv {checkout.parent}/old/.venv\n")
    elif defect == "missing-cfg":
        cfg.unlink()
    elif defect == "unreadable-cfg":
        cfg.chmod(0)
    elif defect == "missing-target":
        cfg.write_text("command = /usr/bin/python3 -m venv\n")
    elif defect == "missing-command":
        cfg.write_text("version = 3.13\n")
    else:
        (checkout / ".venv/bin/python").unlink()
    if changed:
        (checkout / "pyproject.toml").write_text("# dependency changed\n")
    try:
        result = invoke(checkout)
    finally:
        if cfg.exists():
            cfg.chmod(0o644)
    assert result.returncode != 0
    assert "bin/dev-setup" in result.stderr
    assert not (checkout / "calls").exists(), result.stdout + result.stderr


@pytest.mark.parametrize("spaces", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_accepts_local_environment_and_forwards_arguments(checkout: Path, spaces: bool, changed: bool) -> None:
    if spaces:
        checkout = checkout.rename(checkout.with_name("checkout with spaces"))
    (checkout / ".venv/pyvenv.cfg").write_text(f"command = /usr/bin/python3 -m venv --clear {checkout}/.venv\n")
    if changed:
        (checkout / "pyproject.toml").write_text("# dependency changed\n")
    result = invoke(checkout, "tests/test_example.py", "-k", "some test")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (checkout / "calls").read_text()
    assert "<-m> <pytest>" in calls
    assert "<tests/test_example.py> <-k> <some test>" in calls
    # CakeUnbake64 refreshes changed dependencies; RecipeMaker64 retains its toolkit workflow.
    refreshes = "sha256sum pyproject.toml" in (checkout / "bin/test").read_text()
    assert ("pip\n" in calls) == (changed and refreshes)
    if changed and refreshes:
        assert calls.startswith("pip\n")
        digest = hashlib.sha256((checkout / "pyproject.toml").read_bytes()).hexdigest()
        assert (checkout / ".venv/.pyproject.sha256").read_text().startswith(digest)


@pytest.mark.seconds(10)
def test_setup_clears_moved_environment_and_test_accepts_it(checkout: Path, tmp_path: Path) -> None:
    """Use real venv/ensurepip; only the package installer and toolkit discovery are stand-ins."""
    stale = checkout / ".venv/stale-from-old-checkout"
    stale.write_text("must disappear")
    (checkout / ".venv/pyvenv.cfg").write_text("command = /usr/bin/python3 -m venv /old/.venv\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    toolkit_site = tmp_path / "toolkit-site"
    toolkit_site.mkdir()
    for module in ("abucache", "unbake"):
        (toolkit_site / f"{module}.py").write_text("")
    wrapper = tools / "python3"
    pip_stub = '\nfrom pathlib import Path\nwith Path("calls").open("a") as log: log.write("pip\\n")\n'
    executable(
        wrapper,
        f"#!{sys.executable}\n"
        "import json, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open('setup-calls', 'a') as log: log.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['-c', 'import abucache, unbake']: sys.exit(0)\n"
        f"if args[:1] == ['-c']: print({str(toolkit_site)!r}); sys.exit(0)\n"
        f"subprocess.run([{sys.executable!r}, *args], check=True)\n"
        "pip = pathlib.Path('.venv/bin/pip')\n"
        "pathlib.Path('generated-pip').write_text(pip.read_text())\n"
        "header = pip.read_text().split('import ', 1)[0]\n"
        f"pip.write_text(header + {pip_stub!r})\n",
    )
    executable(tools / "decomp", f"#!{wrapper}\n")
    result = invoke(checkout, script="dev-setup", path=tools)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not stale.exists()
    calls = [json.loads(line) for line in (checkout / "setup-calls").read_text().splitlines()]
    assert ["-m", "venv", "--clear", ".venv"] in calls
    assert (checkout / ".venv/pyvenv.cfg").read_text().split("command = ")[1].strip().endswith(f" {checkout}/.venv")
    assert f"{checkout}/.venv/bin/python" in (checkout / "generated-pip").read_text()
    (checkout / "calls").unlink(missing_ok=True)
    fake_python(checkout)
    result = invoke(checkout, "-k", "after setup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "<-m> <pytest>" in (checkout / "calls").read_text()
