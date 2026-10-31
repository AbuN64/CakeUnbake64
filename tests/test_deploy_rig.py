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
NEWER = "d" * 12 + "e" * 28
TOOLKIT = deploy_rig.TOOLKIT
BUILD = f"{TOOLKIT}.{SHA[:12]}"
CHECKOUT = "/srv/runs/board1.card1/Projects/AbuN64/CakeUnbake64"


class Rig:
    """Job Invoke, git and the log reader as the deploy sees them."""

    def __init__(self, activation_exit=0, activation_log=None, readback_exit=0, readback=None, on_gitea=True):
        self.requests = []
        self.commands = []
        self.logs = {
            "/log/1": activation_log
            if activation_log is not None
            else f"suite-checkout: {CHECKOUT}\nsuite-sha: {SHA}\nsuite-import: {CHECKOUT}/src/unbake/__init__.py\n",
            "/log/2": readback if readback is not None else f"readback-pin: unbake {SHA}\n",
        }
        self.exits = [activation_exit, readback_exit]
        self.on_gitea = on_gitea
        self.fetched = None  # what FETCH_HEAD holds: the URL of the last fetch

    def job(self, body):
        self.requests.append(body)
        index = len(self.requests)
        return deploy_rig.Answer(Exit=self.exits[index - 1], Log=f"/log/{index}")

    def git(self, *args):
        self.commands.append(args)
        if args[:1] == ("fetch",):
            if "gitea" in args:  # the checkout the deploy runs in has no remote of that name
                return 128, "fatal: 'gitea' does not appear to be a git repository and the repository exists."
            self.fetched = args[-2]
        if args[:1] == ("merge-base",) and self.fetched == deploy_rig.GITEA and not self.on_gitea:
            return 1, ""
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return 0, SHA
        return 0, ""

    def read(self, path):
        return self.logs[path]

    def deploy(self, sha=SHA):
        return deploy_rig.deploy(sha, "board1", "card1", TOOLKIT, self.job, self.git, self.read)


def test_the_activation_deploys_package_unbake_at_the_landed_sha_then_reads_it_back():
    rig = Rig()
    lines = rig.deploy()
    activation, readback = rig.requests
    assert activation["Worktree"] == f"Projects/AbuN64/CakeUnbake64@{SHA}"
    assert activation["Host"] == "rig"
    assert activation["Activation"] == {"Toolkit": TOOLKIT, "Revision": SHA[:12]}
    assert activation["Argv"][-2:] == [SHA, TOOLKIT]
    assert "tools deploy --package unbake --sha" in activation["Argv"][2]
    assert "pytest" in activation["Argv"][2]
    assert readback["Argv"][-3:] == [SHA, TOOLKIT, BUILD]
    assert "Activation" not in readback
    assert lines[-1] == f"deployed {SHA}"
    assert f"readback-pin: unbake {SHA}" in lines[-2]


def test_main_moved_on_deploys_the_head_and_defers_the_older_deploy():
    log = f"suite-checkout: {CHECKOUT}\nsuite-sha: {NEWER}\nsuite-import: {CHECKOUT}/src/unbake/__init__.py\n"
    rig = Rig(activation_log=log, readback=f"readback-pin: unbake {NEWER}\n")
    rig.git = lambda *args: (0, NEWER) if args[:2] == ("rev-parse", "FETCH_HEAD") else (0, "")
    lines = rig.deploy()
    assert "the older deploy deferred" in lines[0]
    assert rig.requests[0]["Worktree"].endswith(f"@{NEWER}")


def test_both_mains_are_fetched_by_url_never_by_a_remote_name_the_checkout_lacks():
    """8b74bbd: `git fetch gitea main` refused in the checkout the deploy runs in, which has no remote of that name."""
    rig = Rig()
    rig.deploy()
    fetches = [args for args in rig.commands if args[:1] == ("fetch",)]
    assert [args[-2] for args in fetches] == [deploy_rig.GITEA, deploy_rig.GITHUB]
    assert all(args[-1] == "main" for args in fetches)
    assert not any("gitea/main" in args for args in rig.commands)


def test_an_unreachable_gitea_refuses_naming_the_url_before_any_job():
    rig = Rig()
    rig.git = lambda *args: (128, "fatal: unable to access") if args[:1] == ("fetch",) else (0, "")
    with pytest.raises(deploy_rig.Refused, match=r"cannot fetch gitea main \(http"):
        rig.deploy()
    assert rig.requests == []


def test_a_sha_off_gitea_main_refuses_before_any_job():
    rig = Rig(on_gitea=False)
    with pytest.raises(deploy_rig.Refused, match="not on gitea main"):
        rig.deploy()
    assert rig.requests == []


def test_a_failing_suite_refuses_and_names_the_last_line():
    rig = Rig(activation_exit=1, activation_log="FAILED tests/test_x.py::test_y\n")
    with pytest.raises(deploy_rig.Refused, match=r"activation exit 1, links unchanged: FAILED tests/test_x.py::test_y"):
        rig.deploy()
    assert len(rig.requests) == 1


def test_a_suite_that_imported_unbake_outside_its_checkout_refuses():
    log = f"suite-checkout: {CHECKOUT}\nsuite-sha: {SHA}\nsuite-import: /elsewhere/unbake/__init__.py\n"
    with pytest.raises(deploy_rig.Refused, match="outside"):
        Rig(activation_log=log).deploy()


def test_a_suite_at_another_commit_refuses():
    log = f"suite-checkout: {CHECKOUT}\nsuite-sha: {NEWER}\nsuite-import: {CHECKOUT}/src/unbake/__init__.py\n"
    with pytest.raises(deploy_rig.Refused, match="not the landed"):
        Rig(activation_log=log).deploy()


def test_an_already_active_build_skips_the_suite_and_the_readback_still_decides():
    rig = Rig(activation_log=f"{TOOLKIT} is {BUILD}: already active\n")
    assert any("no-op" in line for line in rig.deploy())
    rig = Rig(
        activation_log=f"{TOOLKIT} is {BUILD}: already active\n", readback_exit=1, readback="readback: unbake is x\n"
    )
    with pytest.raises(deploy_rig.Refused, match="readback exit 1: readback: unbake is x"):
        rig.deploy()


def test_job_invoke_refusals_and_bad_answers_are_one_line():
    refusal = json.dumps({"Response": {"Error": {"Code": "Busy", "Message": "no", "NextStep": "wait"}}})
    with pytest.raises(deploy_rig.Refused, match=r"Job Invoke refused Busy .*: no; next: wait"):
        deploy_rig.answer_of(1, refusal)
    with pytest.raises(deploy_rig.Refused, match="no Response"):
        deploy_rig.answer_of(0, "not json")
    with pytest.raises(deploy_rig.Refused, match="no Exit and Log"):
        deploy_rig.answer_of(0, json.dumps({"Response": {"Exit": 0}}))


def test_the_landed_sha_must_be_forty_hex():
    assert deploy_rig.landed_sha({deploy_rig.ENV: SHA}) == SHA
    for bad in ("", "main", SHA[:12]):
        with pytest.raises(deploy_rig.Refused, match=deploy_rig.ENV):
            deploy_rig.landed_sha({deploy_rig.ENV: bad})


def test_main_prints_the_deploy_and_exits_one_on_a_refusal(capsys, monkeypatch):
    args = ["--Subject.Board", "board1", "--Subject.Card", "card1"]
    monkeypatch.setattr(deploy_rig, "deploy", lambda *a: [f"deployed {SHA}"])
    assert deploy_rig.main(args, {deploy_rig.ENV: SHA}) == 0
    assert capsys.readouterr().out == f"deployed {SHA}\n"
    assert deploy_rig.main(args, {}) == 1
    assert capsys.readouterr().err.startswith("deploy refused:")


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
        build = root / f"venv-decomp.{SHA[:12]}"
        wrapper(
            live / "bin/decomp",
            'test "$1 $2 $3 $4 $5 $6" = "tools deploy --package unbake --sha ' + SHA + '" || exit 7\n'
            f'dir="$8.{SHA[:12]}"; mkdir -p "$dir/bin" && ln -sfn "$dir" "$8"',
        )
        toolkit.symlink_to(live)
        done = bash(deploy_rig.BUILD, SHA, str(toolkit), env={"BUILD": str(build), "HOME": str(root)})
        assert done.returncode == 0, done.stdout + done.stderr
        built = root / f"venv-decomp-stage.{SHA[:12]}"
        assert build.resolve() == built.resolve() == stage.resolve()
        assert toolkit.resolve() == live.resolve()  # only Job Invoke's switch makes a build live


def test_a_failing_engine_exits_one_and_links_nothing():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        live, toolkit = root / "live-target", root / "venv-decomp"
        wrapper(live / "bin/decomp", "echo engine said no >&2; exit 3")
        toolkit.symlink_to(live)
        build = root / f"venv-decomp.{SHA[:12]}"
        done = bash(deploy_rig.BUILD, SHA, str(toolkit), env={"BUILD": str(build), "HOME": str(root)})
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
