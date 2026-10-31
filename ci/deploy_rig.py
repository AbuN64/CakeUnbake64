#!/usr/bin/env python3
"""Deploys the landed CakeUnbake64 commit to the rig toolkit through Job Invoke. Run from a run's CakeUnbake64
worktree, whose card has a live run (Job Invoke pushes the commit from there):

  LANDED_Projects_AbuN64_CakeUnbake64=SHA ci/deploy_rig.py --Subject.Board BOARD --Subject.Card CARD

ButlerMaster's close appends the two Subject arguments (the landing card) to the .deploy argv. Unbake is pinned by no
other repository: the toolkit's installed `decomp tools deploy` installs the package `unbake` at SHA and carries every
other installed package over as it is. Any refusal or failed step prints one line on stderr and exits 1. Every rig step
is one `mo-butlermaster Job Invoke` at the exact SHA in a Worktree (JOBS is Dos's width):

  1. the SHA must be on gitea main and on github main; when gitea main has moved past it to a descendant, main's head
     is deployed instead and the log says the older deploy deferred;
  2. the activation job (Toolkit = the live link, Revision = the SHA's first 12 digits, so BUILD is <toolkit>.<sha12>):
     `decomp tools deploy --package unbake --sha SHA` builds a fresh venv through a private `<toolkit>-stage` link,
     BUILD is linked to it, and the full test suite runs in the Worktree with BUILD's python and the Worktree's src
     first on the import path. A failing suite exits non-zero: Job Invoke leaves the live link alone. On exit 0 Job
     Invoke switches the live link to BUILD. When the live link already names BUILD, Job Invoke answers Exit 0 with
     `<link> is <target>: already active` and runs nothing: the readback still decides;
  3. the readback job: the live link resolves to BUILD and the installed commit of unbake equals SHA. A mismatch
     refuses (fix forward, no rollback).
The deploy's answer is the deployed SHA with each job's Exit and Log."""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

ENV = "LANDED_Projects_AbuN64_CakeUnbake64"
REPOSITORY = "Projects/AbuN64/CakeUnbake64"
HOST = "rig"
TOOLKIT = "/home/abu/venv-decomp"
PACKAGE = "unbake"
GITHUB = "https://github.com/AbuN64/cake-unbake-64.git"
SHA = re.compile(r"[0-9a-f]{40}")
ALREADY_ACTIVE = re.compile(r"^\S+ is \S+: already active$", re.MULTILINE)

# git(args) runs git here and returns (exit code, output); job(request) is `mo-butlermaster Job Invoke` and returns its
# answer; read(path) reads a job's Log, which lives on this machine.
Seam = Callable[..., "tuple[int, str]"]
Job = Callable[[dict], "Answer"]
Read = Callable[[str], str]

# BUILD runs inside the activation job with BUILD (<toolkit>.<sha12>) set by Job Invoke. The stage link starts at the
# live toolkit, so the packages `decomp tools deploy` carries over are the live ones; it builds
# `<toolkit>-stage.<sha12>` (or -N when that name is taken) and swaps only the stage link. BUILD becomes a link to that
# venv: Job Invoke's switch is the only way a venv goes live. Arguments: SHA TOOLKIT.
BUILD = r"""set -u
sha=$1 toolkit=$2
: "${BUILD:?Job Invoke sets BUILD for an activation}"
stage=$toolkit-stage
ln -sfn "$(readlink -f "$toolkit")" "$stage" || exit 1
(cd "$HOME" && "$toolkit/bin/decomp" tools deploy --package unbake --sha "$sha" --venv "$stage") \
  || { echo "deploy-error: decomp tools deploy exit $?" >&2; exit 1; }
built=$(readlink -f "$stage")
case $built in
  "$stage.${sha:0:12}" | "$stage.${sha:0:12}"-[0-9]*) ;;
  *) echo "deploy-error: $stage reads $built, not a build of $sha" >&2; exit 1 ;;
esac
ln -sfn "$built" "$BUILD" || exit 1
echo "deploy-build: $built"
"""

# SUITE runs in the Worktree after BUILD, with BUILD set: the full suite on BUILD's python with the Worktree's src
# first on the import path. One `suite-KEY: value` line per fact, then the suite's own output.
SUITE = r"""set -u
echo "suite-checkout: $(pwd -P)"
echo "suite-sha: $(git rev-parse HEAD)"
export PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1
imported=$("$BUILD/bin/python" -c 'import os, unbake; print(os.path.realpath(unbake.__file__))') || exit 9
echo "suite-import: $imported"
"$BUILD/bin/python" -m pytest -q -p no:cacheprovider -n "${JOBS:?Job Invoke sets JOBS}" --rootdir "$PWD" tests
"""

# READBACK runs in the Worktree after the switch. Arguments: SHA TOOLKIT BUILD.
READBACK = r"""set -u
sha=$1 toolkit=$2 build=$3
live=$(readlink -f "$toolkit") || exit 9
want=$(readlink -f "$build") || exit 9
[ "$live" = "$want" ] || { echo "readback: $toolkit reads $live, not $build ($want)" >&2; exit 1; }
"$toolkit/bin/python" - "$sha" <<'PY'
import importlib.metadata
import json
import sys

try:
    direct = json.loads(importlib.metadata.distribution("unbake").read_text("direct_url.json") or "{}")
    installed = direct.get("vcs_info", {}).get("commit_id", "none")
except importlib.metadata.PackageNotFoundError:
    installed = "not installed"
print(f"readback-pin: unbake {installed}", flush=True)
if installed != sys.argv[1]:
    sys.exit(f"readback: unbake is {installed}, the landed commit is {sys.argv[1]}")
PY
"""


class Refused(Exception):
    """One line cause of a refusal or failed step."""


class Answer(dict):
    """The Response of one Job Invoke: Exit, Log, Width, Host and Subject."""


def run(argv: list[str], stdin: str | None = None) -> tuple[int, str]:
    done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def git(*args: str) -> tuple[int, str]:
    return run(["git", *args])


def read_log(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError as error:
        raise Refused(f"cannot read the job log {path}: {error}") from error


def last_line(text: str, default: object) -> str:
    return text.strip().splitlines()[-1] if text.strip() else str(default)


def answer_of(code: int, out: str) -> Answer:
    """Parses `Job Invoke --output=json`: the Response, or Refused with the refusal's Code, Message and NextStep."""
    try:
        response = json.loads(out)["Response"]
    except (ValueError, KeyError, TypeError):
        raise Refused(f"Job Invoke answered no Response (exit {code}): {last_line(out, 'no output')}") from None
    error = response.get("Error") if isinstance(response, dict) else None
    if error or code:
        error = error or {}
        raise Refused(
            f"Job Invoke refused {error.get('Code', 'Fault')} (exit {code}): {error.get('Message', 'no message')}"
            + (f"; next: {error['NextStep']}" if error.get("NextStep") else "")
        )
    if not isinstance(response.get("Exit"), int) or not isinstance(response.get("Log"), str):
        raise Refused(f"Job Invoke answered no Exit and Log: {json.dumps(response)[:200]}")
    return Answer(response)


def job_invoke(body: dict) -> Answer:
    """The public CLI: `mo-butlermaster Job Invoke --input=json` with the request on stdin."""
    stdin = json.dumps({"Metadata": {}, "Request": body})
    return answer_of(*run(["mo-butlermaster", "Job", "Invoke", "--input=json", "--output=json"], stdin))


def landed_sha(environ: dict[str, str]) -> str:
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    return sha


def newest_main(sha: str, git: Seam) -> str:
    """The SHA to deploy: gitea main's head when it descends from sha (sha itself when it is the head), else Refused.
    The head must also be on github main. Gitea main is this repository's `gitea` remote."""
    code, out = git("fetch", "-q", "--no-tags", "gitea", "main")
    if code:
        raise Refused(f"cannot fetch gitea main: {last_line(out, code)}")
    if git("merge-base", "--is-ancestor", sha, "gitea/main")[0]:
        raise Refused(f"{sha[:12]} is not on gitea main")
    code, out = git("rev-parse", "gitea/main")
    head = out if code == 0 and SHA.fullmatch(out) else sha
    code, out = git("fetch", "-q", "--no-tags", GITHUB, "main")
    if code:
        raise Refused(f"cannot fetch github main: {last_line(out, code)}")
    if git("merge-base", "--is-ancestor", head, "FETCH_HEAD")[0]:
        raise Refused(f"{head[:12]} is not on github main")
    return head


def have_commit(sha: str, git: Seam) -> None:
    """Job Invoke pushes the commit from this repository: it must be here (newest_main fetched gitea main)."""
    if git("cat-file", "-e", f"{sha}^{{commit}}")[0]:
        raise Refused(f"{sha[:12]} is not in this repository, so Job Invoke cannot push it to the rig")


def request(board: str, card: str, sha: str, argv: list[str], activation: dict[str, str] | None = None) -> dict:
    body = {"Subject": {"Board": board, "Card": card}, "Host": HOST, "Worktree": f"{REPOSITORY}@{sha}", "Argv": argv}
    return {**body, "Activation": activation} if activation else body


def suite_facts(text: str) -> dict[str, str]:
    facts = {}
    for line in text.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.startswith("suite-"):
            facts[key] = value
    return facts


def checked_suite(sha: str, log: str, text: str) -> list[str]:
    """The suite facts of the activation job's log: each must hold, else Refused."""
    facts = suite_facts(text)
    for key in ("suite-checkout", "suite-sha", "suite-import"):
        if key not in facts:
            raise Refused(f"suite gate did not finish at {sha[:12]} ({log}): {last_line(text, 'no output')}")
    if facts["suite-sha"] != sha:
        raise Refused(f"suite ran at {facts['suite-sha']}, not the landed {sha}")
    if not PurePosixPath(facts["suite-import"]).is_relative_to(facts["suite-checkout"]):
        raise Refused(f"suite imported {PACKAGE} from {facts['suite-import']}, outside {facts['suite-checkout']}")
    return [f"{sha[:12]}: suite sha {sha}", f"{sha[:12]}: suite imported {facts['suite-import']}"]


def deploy(sha: str, board: str, card: str, toolkit: str, job: Job, git: Seam, read: Read) -> list[str]:
    """Deploys the newest landed main; returns the log lines to print. Raises Refused on the first failed step."""
    lines = []
    head = newest_main(sha, git)
    if head != sha:
        lines.append(f"{sha[:12]}: main has moved to {head[:12]}, deploying main's head; the older deploy deferred")
        sha = head
    have_commit(sha, git)
    revision = sha[:12]
    build = f"{toolkit}.{revision}"
    argv = ["bash", "-c", BUILD + SUITE, "deploy", sha, toolkit]
    activation = job(request(board, card, sha, argv, {"Toolkit": toolkit, "Revision": revision}))
    log = activation["Log"]
    lines.append(f"{revision}: activation job Exit {activation['Exit']}, Log {log}")
    text = read(log)
    if activation["Exit"]:
        raise Refused(f"activation exit {activation['Exit']}, links unchanged: {last_line(text, 'no output')} ({log})")
    if ALREADY_ACTIVE.search(text):
        lines.append(f"{revision}: activation was a no-op, the live link already names the build; suite gate skipped")
    else:
        lines += checked_suite(sha, log, text)
    readback = job(request(board, card, sha, ["bash", "-c", READBACK, "readback", sha, toolkit, build]))
    lines.append(f"{revision}: readback job Exit {readback['Exit']}, Log {readback['Log']}")
    final = read(readback["Log"])
    if readback["Exit"]:
        raise Refused(f"readback exit {readback['Exit']}: {last_line(final, 'no output')} ({readback['Log']})")
    return [*lines, f"{revision}: {toolkit} reads {build}; {last_line(final, '')}", f"deployed {sha}"]


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy the landed CakeUnbake64 commit to the rig toolkit.")
    parser.add_argument("--Subject.Board", dest="board", required=True)
    parser.add_argument("--Subject.Card", dest="card", required=True)
    args = parser.parse_args(argv)
    try:
        sha = landed_sha(dict(os.environ) if environ is None else environ)
        for line in deploy(sha, args.board, args.card, TOOLKIT, job_invoke, git, read_log):
            print(line, flush=True)
    except Refused as refusal:
        print(f"deploy refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
