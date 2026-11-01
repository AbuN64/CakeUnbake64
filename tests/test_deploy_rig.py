"""ci/deploy_rig.py with Job Invoke, git and the log reader mocked."""

import importlib.util
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("deploy_rig", Path(__file__).parent.parent / "ci" / "deploy_rig.py")
deploy_rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_rig)

SHA = "a" * 12 + "b" * 28
TOOLKIT = "/home/abu/venv-unbake"
NAME = deploy_rig.name(SHA)  # a digest of the one requirement; never the SHA
ENVIRON = {deploy_rig.ENV: SHA, "TOOLKIT": TOOLKIT}
ARGS = ["--Subject.Board", "board1", "--Subject.Card", "card1"]  # the close appends them; no step reads them


class Bash:
    """The seam: records each script run with its arguments and answers (exit code, stdout) from a queue."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, script, *args, capture=False):
        self.calls.append((script, args, capture))
        return self.answers.pop(0)


def main(step, bash, environ=ENVIRON):
    code = deploy_rig.main([step, *ARGS], environ, bash)
    return code


def test_name_is_a_12_hex_digest_of_the_requirement_and_runs_no_script(capsys):
    bash = Bash()
    assert main("name", bash) == 0
    assert f"name: {NAME}" in capsys.readouterr().out
    assert re.fullmatch(r"[0-9a-f]{12}", NAME)
    assert SHA[:12] != NAME and deploy_rig.name("c" * 40) != NAME
    assert bash.calls == []


def test_build_runs_build_and_suite_with_the_name_and_url():
    bash = Bash((0, ""))
    assert main("build", bash) == 0
    script, args, capture = bash.calls[0]
    assert (args, capture) == ((SHA, TOOLKIT, NAME, deploy_rig.URL), False)
    assert script == deploy_rig.BUILD + deploy_rig.SUITE


def test_a_failing_build_or_suite_refuses_with_the_links_unchanged(capsys):
    assert main("build", Bash((1, ""))) == 1
    assert "build exit 1, links unchanged" in capsys.readouterr().err


def test_readback_reads_the_build_linked_for_the_landed_sha(capsys):
    bash = Bash((0, ""))
    assert main("readback", bash) == 0
    assert bash.calls == [(deploy_rig.READBACK, (SHA, TOOLKIT, f"{TOOLKIT}.{SHA}"), False)]
    assert main("readback", Bash((1, ""))) == 1
    assert "readback exit 1" in capsys.readouterr().err


@pytest.mark.parametrize("step", ["deploy", "serve", ""])
def test_an_unknown_or_missing_step_refuses_before_any_script(step, capsys):
    bash = Bash()
    assert main(step, bash) == 1
    assert "the step is one of" in capsys.readouterr().err
    assert bash.calls == []


@pytest.mark.parametrize(
    ("environ", "want"),
    [
        ({}, "is not a 40-hex SHA"),
        ({deploy_rig.ENV: "abc", "TOOLKIT": TOOLKIT}, "is not a 40-hex SHA"),
        ({deploy_rig.ENV: SHA}, "TOOLKIT is not an absolute path"),
        ({deploy_rig.ENV: SHA, "TOOLKIT": "venv"}, "TOOLKIT is not an absolute path"),
    ],
)
def test_the_landed_sha_must_be_forty_hex_and_the_toolkit_absolute(environ, want, capsys):
    bash = Bash()
    assert main("name", bash, environ) == 1
    assert want in capsys.readouterr().err
    assert bash.calls == []


def test_the_orchestrator_is_gone():
    text = (Path(__file__).parent.parent / "ci" / "deploy_rig.py").read_text()
    for gone in ("job_invoke", "answer_of", "newest_main", "have_commit", "Subject.Board", "def deploy(", "Job Invoke"):
        assert gone not in text, gone


def test_the_deploy_file_is_the_step_grammar():
    root = Path(__file__).parent.parent
    lines = (root / ".deploy").read_text().splitlines()
    assert lines == [
        f"deploy-env TOOLKIT={TOOLKIT}",
        "rig run ci/deploy_rig.py name",
        f"rig activate ci/deploy_rig.py {TOOLKIT} build",
        "rig run ci/deploy_rig.py readback",
    ]
    assert os.access(root / "ci" / "deploy_rig.py", os.X_OK)


def wrapper(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


def bash(script, *args, env=None, cwd=None):
    return subprocess.run(
        ["bash", "-c", script, "t", *args],
        env={"PATH": os.environ["PATH"], **(env or {})},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_build_script_installs_unbake_at_the_sha_into_a_fresh_venv_and_links_build():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        py = root / "bin/python3"
        wrapper(
            py,
            'if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; exit 0; fi\n'
            'echo "$@" > "$(dirname "$0")/argv"',
        )
        toolkit, build = root / "venv-unbake", root / f"venv-unbake.{SHA}"
        done = bash(
            deploy_rig.BUILD,
            SHA,
            str(toolkit),
            NAME,
            "https://example.lan/u",
            env={"BUILD": str(build), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
        )
        assert done.returncode == 0, done.stdout + done.stderr
        built = root / f"venv-unbake.{NAME}"
        assert build.resolve() == built.resolve() and not toolkit.exists()  # only Job Invoke's switch makes it live
        assert f"unbake[dev] @ git+https://example.lan/u@{SHA}" in (built / "bin/argv").read_text()


def test_a_failing_install_exits_one_and_links_nothing():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        wrapper(
            root / "bin/python3",
            'if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; printf "#!/bin/sh\\nexit 3\\n" > "$3/bin/python"; '
            'chmod +x "$3/bin/python"; fi',
        )
        build = root / f"venv-unbake.{SHA}"
        done = bash(
            deploy_rig.BUILD,
            SHA,
            str(root / "venv-unbake"),
            NAME,
            "u",
            env={"BUILD": str(build), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
        )
        assert done.returncode == 1 and "deploy-error: pip install exit 3" in done.stderr
        assert not build.is_symlink()


def test_the_readback_script_checks_the_link_then_the_installed_commit():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live = root / "live"
        toolkit, other = root / "venv-unbake", root / "other"
        other.mkdir()
        toolkit.symlink_to(live)
        live.mkdir()
        done = bash(deploy_rig.READBACK, SHA, str(toolkit), str(other))
        assert done.returncode == 1 and "not" in done.stderr


def test_the_toolkit_path_is_this_repos_own_and_no_script_names_the_decomp_toolkit():
    root = Path(__file__).parent.parent
    for path in (root / ".deploy", root / "ci/deploy_rig.py"):
        assert "venv-decomp" not in path.read_text(), path
        assert "decomp tools" not in path.read_text(), path


def test_the_suite_refuses_a_tree_at_another_commit_than_the_landed_one():
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
        for args in (["init", "-q", "-b", "main"], [*identity, "commit", "-q", "--allow-empty", "-m", "c"]):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        done = bash(deploy_rig.SUITE, SHA, cwd=repo, env={"BUILD": str(repo)})
        assert done.returncode == 1
        assert "not the landed" in done.stderr
        assert "suite-sha:" in done.stdout
