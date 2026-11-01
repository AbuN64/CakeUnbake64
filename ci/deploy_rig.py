#!/usr/bin/env python3
"""The rig deploy steps of CakeUnbake64: one step per `.deploy` line, each an entry point of this file. The close runs
them in file order on the rig at the landed revision, from the root of the tree, with
LANDED_Projects_AbuN64_CakeUnbake64 set and `deploy-env TOOLKIT=<live link>` in the environment; it appends its own
landing arguments, which no step reads.
This repository owns its own rig toolkit, /home/abu/venv-unbake, and no other repository names that path: AbuDecomp64
owns the decomp toolkit, and a consumer that needs both puts both on PATH. A step prints its facts and exits 0, or
prints one line on stderr and exits 1; it calls no other tool of the close and none of another repository's toolkit.

  name       the build's NAME, the first 12 hex digits of the sha256 of the requirement `unbake[dev] @ git+URL@SHA`
             (every dependency is pinned in pyproject.toml, so the landed commit is the whole content);
  build      the activation (`activate`, BUILD is <toolkit>.<SHA>): a fresh venv `<toolkit>.<NAME>` is created and
             `pip install` installs unbake at the landed SHA into it, BUILD is linked to that venv, and the full test
             suite runs in the tree with BUILD's python and the tree's src first on the import path. A failing suite
             exits non-zero and the live link stays; on exit 0 the close switches the live link to BUILD;
  readback   the live link resolves to BUILD and the installed commit of unbake equals the landed SHA. A mismatch
             refuses (fix forward, no rollback).

  deploy-env TOOLKIT=/home/abu/venv-unbake
  rig run ci/deploy_rig.py name
  rig activate ci/deploy_rig.py /home/abu/venv-unbake build
  rig run ci/deploy_rig.py readback"""

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Callable

ENV = "LANDED_Projects_AbuN64_CakeUnbake64"
URL = "https://github.com/AbuN64/cake-unbake-64"
SHA = re.compile(r"[0-9a-f]{40}")

# bash(script, args) runs one of the scripts below and returns its exit code and, when captured, its stdout.
Bash = Callable[..., "tuple[int, str]"]

# BUILD runs inside the activation job with BUILD (<toolkit>.<sha>) set by the close. It creates the fresh venv
# `<toolkit>.<name>` with the python3 of the job, installs unbake at the landed commit into it with its pinned
# dependencies, and links BUILD to it: the close's switch is the only way a venv goes live. Arguments: SHA TOOLKIT NAME URL.
BUILD = r"""set -u
sha=$1 toolkit=$2 name=$3 url=$4
: "${BUILD:?the close sets BUILD for an activation}"
built=$toolkit.$name
[ -e "$built" ] && { echo "deploy-error: $built exists" >&2; exit 1; }
python3 -m venv "$built" || { echo "deploy-error: venv exit $?" >&2; exit 1; }
"$built/bin/python" -m pip install -q "unbake[dev] @ git+$url@$sha" \
  || { echo "deploy-error: pip install exit $?" >&2; exit 1; }
ln -sfn "$built" "$BUILD" || exit 1
echo "deploy-build: $built"
"""

# SUITE runs in the tree after BUILD (Arguments: SHA), with BUILD set: the full suite on BUILD's python with the
# tree's src first on the import path. One `suite-KEY: value` line per fact, then the suite's own output.
SUITE = r"""set -u
sha=$1
echo "suite-checkout: $(pwd -P)"
echo "suite-sha: $(git rev-parse HEAD)"
head=$(git rev-parse HEAD)
[ "$head" = "$sha" ] || { echo "suite: the tree is at $head, not the landed $sha" >&2; exit 1; }
export PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1
imported=$("$BUILD/bin/python" -c 'import os, unbake; print(os.path.realpath(unbake.__file__))') || exit 9
echo "suite-import: $imported"
"$BUILD/bin/python" -m pytest -q -p no:cacheprovider -n "${JOBS:?the close sets JOBS}" --rootdir "$PWD" tests
"""

# READBACK runs in the tree after the switch. Arguments: SHA TOOLKIT BUILD.
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


def bash(script: str, *args: str, capture: bool = False) -> tuple[int, str]:
    done = subprocess.run(
        ["bash", "-c", script, "step", *args], stdout=subprocess.PIPE if capture else None, text=True, check=False
    )
    return done.returncode, done.stdout or ""


def last_line(text: str, default: object) -> str:
    return text.strip().splitlines()[-1] if text.strip() else str(default)


def landed(environ: dict[str, str]) -> tuple[str, str]:
    """The landed SHA and the live toolkit link, each from its own explicit variable."""
    sha = environ.get(ENV, "")
    if not SHA.fullmatch(sha):
        raise Refused(f"{ENV} is not a 40-hex SHA: {sha!r}")
    toolkit = environ.get("TOOLKIT", "")
    if not toolkit.startswith("/"):
        raise Refused(f"TOOLKIT is not an absolute path (a `deploy-env TOOLKIT=<live link>` line): {toolkit!r}")
    return sha, toolkit


def name(sha: str) -> str:
    """The build's name: a digest of its one requirement, the unbake commit (its dependencies are pinned in it)."""
    return hashlib.sha256(f"unbake[dev] @ git+{URL}@{sha}".encode()).hexdigest()[:12]


def step_name(sha: str, toolkit: str, run: Bash) -> None:
    sys.stdout.write(f"name: {name(sha)}\n")
    sys.stdout.flush()


def step_build(sha: str, toolkit: str, run: Bash) -> None:
    code, _ = run(BUILD + SUITE, sha, toolkit, name(sha), URL)
    if code:
        raise Refused(f"build exit {code}, links unchanged")


def step_readback(sha: str, toolkit: str, run: Bash) -> None:
    code, _ = run(READBACK, sha, toolkit, f"{toolkit}.{sha}")
    if code:
        raise Refused(f"readback exit {code}")


STEPS = {"name": step_name, "build": step_build, "readback": step_readback}


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None, run: Bash = bash) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        step = STEPS.get(argv[0] if argv else "")
        if step is None:
            raise Refused(f"the step is one of {', '.join(STEPS)}, not {argv[0] if argv else 'nothing'!r}")
        step(*landed(dict(os.environ) if environ is None else environ), run)
    except Refused as refusal:
        sys.stderr.write(f"deploy refused: {refusal}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
