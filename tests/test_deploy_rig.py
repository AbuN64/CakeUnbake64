"""ci/deploy_rig.py with Job Invoke, git and the log reader mocked."""

import atexit
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REAL_GIT = "the rig suite script reads the tree's commit with git, as on the rig"

spec = importlib.util.spec_from_file_location("deploy_rig", Path(__file__).parent.parent / "ci" / "deploy_rig.py")
deploy_rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_rig)

SHA = "a" * 12 + "b" * 28
HOME = tempfile.mkdtemp(prefix="deploy-rig-home-")  # a step publishes beside its toolkit: always a mocked home
atexit.register(shutil.rmtree, HOME, ignore_errors=True)
TOOLKIT = f"{HOME}/venv-unbake"
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
    assert (args, capture) == ((SHA, TOOLKIT, NAME), False)
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
        "deploy-input TOOLKIT",
        "rig run ci/deploy_rig.py name",
        "rig activate ci/deploy_rig.py $TOOLKIT build",
        "rig run ci/deploy_rig.py readback",
    ]
    assert "/home" not in (root / ".deploy").read_text()  # the value lives in Hosts.json, not in a tracked file
    assert "/home" not in (root / "ci" / "deploy_rig.py").read_text()
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
            cwd=root,
            env={"BUILD": str(build), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
        )
        assert done.returncode == 0, done.stdout + done.stderr
        built = root / f"venv-unbake.{NAME}"
        assert build.resolve() == built.resolve() and not toolkit.exists()  # only Job Invoke's switch makes it live
        # the landed tree (the job's working directory) is the source, never a mirror
        assert f"unbake[dev] @ git+file://{root.resolve()}@{SHA}" in (built / "bin/argv").read_text()


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
            cwd=root,
            env={"BUILD": str(build), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
        )
        assert done.returncode == 1 and "deploy-error: pip install exit 3" in done.stderr
        assert not build.is_symlink()
        assert not (root / f"venv-unbake.{NAME}").exists()  # a failed build leaves no <toolkit>.<name>
        assert sorted(p.name for p in root.iterdir()) == ["bin"]  # nor its temporary sibling


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


@pytest.mark.integration(REAL_GIT)
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


def test_publish_links_each_command_into_the_command_dir_through_the_live_link(tmp_path):
    build = tmp_path / "venv-unbake.abc"
    for command in deploy_rig.PUBLISHED:
        wrapper(build / "bin" / command, "exit 0")
    toolkit = tmp_path / "venv-unbake"
    toolkit.symlink_to(build)
    assert deploy_rig.publish(str(toolkit)) == ["unbake", "unbake-run"]
    folder = tmp_path / deploy_rig.COMMANDS
    for command in deploy_rig.PUBLISHED:
        assert os.readlink(folder / command) == f"{toolkit}/bin/{command}"  # the stable link, not the build
        assert (folder / command).resolve() == (build / "bin" / command).resolve()
    other = tmp_path / "venv-unbake.def"
    for command in deploy_rig.PUBLISHED:
        wrapper(other / "bin" / command, "exit 0")
    toolkit.unlink()
    toolkit.symlink_to(other)  # a later switch is followed without a second publish
    assert (folder / "unbake").resolve() == (other / "bin" / "unbake").resolve()
    assert deploy_rig.publish(str(toolkit)) == ["unbake", "unbake-run"]  # idempotent


def test_publish_refuses_a_command_that_resolves_outside_the_live_toolkit(tmp_path):
    (tmp_path / "venv-unbake.abc" / "bin").mkdir(parents=True)
    (tmp_path / "venv-unbake").symlink_to(tmp_path / "venv-unbake.abc")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "venv-unbake.abc" / "bin" / "unbake").symlink_to(elsewhere / "unbake")
    with pytest.raises(deploy_rig.Refused, match="outside the live toolkit"):
        deploy_rig.publish(str(tmp_path / "venv-unbake"))


def test_only_the_readback_prunes_after_it_holds():
    text = (Path(__file__).parent.parent / "ci" / "deploy_rig.py").read_text()
    for gone in ("def force_remove", "unseal"):
        assert gone not in text, gone
    assert text.count("prune(toolkit)") == 1 and text.index("prune(toolkit)") > text.index("def step_readback")


def fake_python(root):
    """A python3 whose venv has a bin/python that fails `-m pip` while root/fail exists, else records it."""
    wrapper(
        root / "bin/python3",
        'if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; '
        'printf "#!%s/bin/python\\n" "$3" > "$3/bin/unbake"; chmod +x "$3/bin/unbake"; '
        'printf "VIRTUAL_ENV=%s\\n" "$3" > "$3/bin/activate"; exit 0; fi\n'
        '[ -e "$(dirname "$0")/../../fail" ] && exit 3\n'
        'echo "$@" > "$(dirname "$0")/argv"',
    )


def deploy_build(root):
    return bash(
        deploy_rig.BUILD,
        SHA,
        str(root / "venv-unbake"),
        NAME,
        cwd=root,
        env={"BUILD": str(root / f"venv-unbake.{SHA}"), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
    )


def test_the_1102_sequence_a_mirror_without_the_sha_cannot_fail_and_a_failed_pip_blocks_nothing(tmp_path):
    """11-02: pip failed fetching a commit the GitHub mirror lacked and left venv-unbake.<name>; the next two deploys
    refused 'exists'. Now the source is the landed tree, and a failed install leaves nothing."""
    fake_python(tmp_path / "pyroot")
    root = tmp_path / "pyroot"
    assert "github" not in deploy_rig.BUILD.lower() and not hasattr(deploy_rig, "URL")
    (root / "fail").write_text("")
    first = deploy_build(root)
    assert first.returncode == 1 and "pip install exit 3" in first.stderr
    assert not (root / f"venv-unbake.{NAME}").exists()
    assert not (root / f"venv-unbake.{SHA}").is_symlink()
    (root / "fail").unlink()
    second = deploy_build(root)  # the same sha proceeds
    assert second.returncode == 0, second.stderr
    built = root / f"venv-unbake.{NAME}"
    assert (root / f"venv-unbake.{SHA}").resolve() == built.resolve()
    again = deploy_build(root)  # a finished build of the sha is never overwritten
    assert again.returncode == 1 and "exists" in again.stderr


def test_the_renamed_venv_scripts_name_the_final_path_not_the_temporary_sibling(tmp_path):
    fake_python(tmp_path)
    done = deploy_build(tmp_path)
    assert done.returncode == 0, done.stderr
    built = tmp_path / f"venv-unbake.{NAME}"
    assert (built / "bin/unbake").read_text() == f"#!{built}/bin/python\n"
    assert (built / "bin/activate").read_text() == f"VIRTUAL_ENV={built}\n"
    assert ".tmp." not in "".join(p.read_text() for p in (built / "bin").iterdir() if p.name != "python")
    assert os.access(built / "bin/unbake", os.X_OK)


def test_prune_keeps_the_live_and_previous_builds_and_removes_the_rest(tmp_path):
    names = {k: f"{i:012x}" for i, k in enumerate(["live", "previous", "old1", "old2", "new"])}
    for name in names.values():
        (tmp_path / f"venv-unbake.{name}" / "bin").mkdir(parents=True)
    (tmp_path / f"venv-unbake.{names['old1']}.tmp.AbC123").mkdir()  # a build killed before its rename
    (tmp_path / "venv-unbake").symlink_to(f"venv-unbake.{names['live']}")
    (tmp_path / "venv-unbake.previous").symlink_to(f"venv-unbake.{names['previous']}")
    (tmp_path / f"venv-unbake.{'1' * 40}").symlink_to(f"venv-unbake.{names['old1']}")  # a BUILD link, dangling after
    (tmp_path / f"venv-unbake.{'2' * 40}").symlink_to(f"venv-unbake.{names['live']}")  # still resolves: kept
    for other in ("venv-unbake-stage", "venv-decomp." + "a" * 12, "venv-unbake.pins"):
        (tmp_path / other).mkdir()
    removed = deploy_rig.prune(str(tmp_path / "venv-unbake"))
    assert sorted(removed) == sorted(
        [f"venv-unbake.{names[k]}" for k in ("old1", "old2", "new")]
        + [f"venv-unbake.{names['old1']}.tmp.AbC123", f"venv-unbake.{'1' * 40}"]
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [
            "venv-unbake",
            "venv-unbake.previous",
            f"venv-unbake.{names['live']}",
            f"venv-unbake.{names['previous']}",
            f"venv-unbake.{'2' * 40}",
            "venv-unbake-stage",
            "venv-decomp." + "a" * 12,
            "venv-unbake.pins",
        ]
    )


def test_readback_prunes_only_after_the_readback_holds(tmp_path, capsys):
    toolkit = tmp_path / "venv-unbake"
    live, old = tmp_path / f"venv-unbake.{'1' * 12}", tmp_path / f"venv-unbake.{'2' * 12}"
    for build in (live, old):
        for command in deploy_rig.PUBLISHED:
            wrapper(build / "bin" / command, "exit 0")
    toolkit.symlink_to(live)
    environ = {deploy_rig.ENV: SHA, "TOOLKIT": str(toolkit)}
    assert deploy_rig.main(["readback"], environ, Bash((1, ""))) == 1
    assert old.exists()  # a refused readback removes nothing
    assert deploy_rig.main(["readback"], environ, Bash((0, ""))) == 0
    assert not old.exists() and live.exists()
    assert "prune: venv-unbake." + "2" * 12 in capsys.readouterr().out
