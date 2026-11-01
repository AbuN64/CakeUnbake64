"""ci/deploy_rig.py with Job Invoke, git and the log reader mocked."""

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("deploy_rig", Path(__file__).parent.parent / "ci" / "deploy_rig.py")
deploy_rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_rig)

SHA = "a" * 12 + "b" * 28
TOOLKIT = "/home/abu/venv-decomp"
NAME = "0123456789ab"  # the engine's digest of the whole content; never the SHA
ENVIRON = {deploy_rig.ENV: SHA, "TOOLKIT": TOOLKIT}
ENVELOPE = json.dumps({"result": {"name": NAME}}) + "\n"
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


def test_name_prints_the_engines_name(capsys):
    bash = Bash((0, "noise\n" + ENVELOPE))
    assert main("name", bash) == 0
    assert f"name: {NAME}" in capsys.readouterr().out
    assert bash.calls == [(deploy_rig.NAME, (SHA, TOOLKIT), True)]


def test_a_failed_name_refuses_and_stops_build_before_any_build(capsys):
    assert main("name", Bash((1, "name-error\n"))) == 1
    assert "name exit 1" in capsys.readouterr().err
    bash = Bash((1, ""))
    assert main("build", bash) == 1
    assert len(bash.calls) == 1


def test_build_names_the_build_then_runs_build_and_suite_with_the_name():
    bash = Bash((0, ENVELOPE), (0, ""))
    assert main("build", bash) == 0
    script, args, capture = bash.calls[1]
    assert (args, capture) == ((SHA, TOOLKIT, NAME), False)
    assert script == deploy_rig.BUILD + deploy_rig.SUITE


def test_a_failing_build_or_suite_refuses_with_the_links_unchanged(capsys):
    assert main("build", Bash((0, ENVELOPE), (1, ""))) == 1
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


def test_the_build_script_links_build_to_the_stage_venv_and_never_touches_the_live_link():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live, toolkit, stage = root / "live-target", root / "venv-decomp", root / "venv-decomp-stage"
        build = root / f"venv-decomp.{NAME}"
        wrapper(
            live / "bin/decomp",
            'test "$1 $2 $3 $4 $5 $6" = "tools deploy --package unbake --sha ' + SHA + '" || exit 7\n'
            f'dir="$8.{NAME}"; mkdir -p "$dir/bin" && ln -sfn "$dir" "$8"',
        )
        toolkit.symlink_to(live)
        done = bash(deploy_rig.BUILD, SHA, str(toolkit), NAME, env={"BUILD": str(build), "HOME": str(root)})
        assert done.returncode == 0, done.stdout + done.stderr
        built = root / f"venv-decomp-stage.{NAME}"
        assert build.resolve() == built.resolve() == stage.resolve()
        assert toolkit.resolve() == live.resolve()  # only Job Invoke's switch makes a build live


def test_a_failing_engine_exits_one_and_links_nothing():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live, toolkit = root / "live-target", root / "venv-decomp"
        wrapper(live / "bin/decomp", "echo engine said no >&2; exit 3")
        toolkit.symlink_to(live)
        build = root / f"venv-decomp.{NAME}"
        done = bash(deploy_rig.BUILD, SHA, str(toolkit), NAME, env={"BUILD": str(build), "HOME": str(root)})
        assert done.returncode == 1
        assert "deploy-error: decomp tools deploy exit 3" in done.stderr
        assert not build.is_symlink()


def test_the_readback_script_checks_the_link_then_the_installed_commit():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live = root / "live"
        toolkit, other = root / "venv-decomp", root / "other"
        other.mkdir()
        toolkit.symlink_to(live)
        live.mkdir()
        done = bash(deploy_rig.READBACK, SHA, str(toolkit), str(other))
        assert done.returncode == 1 and "not" in done.stderr


def test_the_name_script_runs_the_toolkits_engine_over_the_live_link_and_a_failure_exits_one():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live, toolkit = root / "live-target", root / "venv-decomp"
        wrapper(
            live / "bin/decomp",
            f'test "$*" = "tools name --package unbake --sha {SHA} --carry-from {toolkit}" || exit 7\n'
            f"echo '{json.dumps({'result': {'name': NAME}})}'",
        )
        toolkit.symlink_to(live)
        done = bash(deploy_rig.NAME, SHA, str(toolkit), env={"HOME": str(root)})
        assert done.returncode == 0, done.stdout + done.stderr
        assert deploy_rig.build_name(done.stdout) == NAME
        wrapper(live / "bin/decomp", "exit 2")
        failed = bash(deploy_rig.NAME, SHA, str(toolkit), env={"HOME": str(root)})
        assert failed.returncode == 1
        assert "deploy AbuDecomp64 first" in failed.stderr


def test_the_build_name_is_the_envelopes_12_hex_name_and_anything_else_is_refused():
    assert deploy_rig.build_name('noise\n{"result": {"name": "abcdef012345"}}\n') == "abcdef012345"
    for bad in ("", "no json", '{"result": {}}', '{"result": {"name": "../x"}}'):
        with pytest.raises(deploy_rig.Refused, match="no build name"):
            deploy_rig.build_name(bad)


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
