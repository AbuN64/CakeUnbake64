#!/usr/bin/env python3
"""The rig deploy steps of CakeUnbake64: one step per `.deploy` line, each an entry point of this file. The close runs
them in file order on the rig at the landed revision, from the root of the tree, with
LANDED_Projects_AbuN64_CakeUnbake64 set and `deploy-env TOOLKIT=<live link>` in the environment; it appends its own
landing arguments, which no step reads.
This repository owns its own rig toolkit, the live link `deploy-env TOOLKIT=` names, and no other repository names it.
Its commands (unbake, unbake-run) are published by name into the one command directory `commands` beside the toolkit
link, the directory the rig job PATH holds; a consumer calls them by name only. A step prints its facts and exits 0, or
prints one line on stderr and exits 1; it calls no other tool of the close and none of another repository's toolkit.

  name       the build's NAME, the first 12 hex digits of the sha256 of the requirement `unbake[dev] @ <SHA>`
             (every dependency is pinned in pyproject.toml, so the landed commit is the whole content);
  build      the activation (`activate`, BUILD is <toolkit>.<SHA>): `pip install` installs unbake at the landed SHA
             from the landed tree itself (`git+file://<tree>@<SHA>`; never the GitHub mirror, which can lack a fresh
             commit) into a fresh venv created as a temporary sibling `<toolkit>.<NAME>.tmp.*`. Only after the install
             succeeded the sibling is made relocatable (its scripts name the final path) and renamed to
             `<toolkit>.<NAME>`, so a failed build leaves no `<toolkit>.<NAME>` and the next deploy of the same SHA
             proceeds. BUILD is linked to that venv and the full test suite runs in the tree with BUILD's python and
             the tree's src first on the import path. A failing suite exits non-zero, the live link stays and the
             failed build's folder and link are removed at once; on exit 0 the close switches the live link to BUILD;
  readback   the live link resolves to BUILD and the installed commit of unbake equals the landed SHA. A mismatch
             refuses (fix forward, no rollback). After it holds, each command is linked into the command directory
             (`commands/unbake` -> `<toolkit>/bin/unbake`, through the live link, so it follows every later switch) and
             read back: it resolves into the live toolkit. Then every other build venv `<toolkit>.<12 hex>` is
             removed, keeping the live one and `<toolkit>.previous`, with any leftover temporary sibling and any
             `<toolkit>.<SHA>` link left dangling. Nothing is removed before the readback holds.

  deploy-env TOOLKIT=<live link>
  rig run ci/deploy_rig.py name
  rig activate ci/deploy_rig.py <live link> build
  rig run ci/deploy_rig.py readback"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable

ENV = "LANDED_Projects_AbuN64_CakeUnbake64"
REQUIREMENT = "unbake[dev]"  # the one requirement, installed from the landed tree at the landed SHA
SHA = re.compile(r"[0-9a-f]{40}")
COMMANDS = "commands"  # the command directory beside the toolkit link
PUBLISHED = ("unbake", "unbake-run")  # this repository's [project.scripts]
BUILT = re.compile(r"[0-9a-f]{12}(\.tmp\..+)?")  # a build venv suffix, or its temp sibling
LINK = re.compile(r"[0-9a-f]{40}")  # the suffix of a BUILD link `<toolkit>.<SHA>`

# bash(script, args) runs one of the scripts below and returns its exit code and, when captured, its stdout.
Bash = Callable[..., "tuple[int, str]"]

# BUILD runs inside the activation job, in the landed tree, with BUILD (<toolkit>.<sha>) set by the close. It creates
# the fresh venv as the temporary sibling `<toolkit>.<name>.tmp.*`, installs unbake at the landed commit from the tree
# into it with its pinned dependencies, makes the sibling's scripts name the final path (a venv hard-codes its own
# path in every shebang) and renames it to `<toolkit>.<name>` only then, so no failure leaves `<toolkit>.<name>`
# behind; the trap removes the sibling on any exit before the rename. It links BUILD to the venv: the close's switch
# is the only way a venv goes live. Arguments: SHA TOOLKIT NAME.
BUILD = r"""set -u
sha=$1 toolkit=$2 name=$3
: "${BUILD:?the close sets BUILD for an activation}"
built=$toolkit.$name
[ -e "$built" ] && { echo "deploy-error: $built exists" >&2; exit 1; }
tmp=$(mktemp -d "$built.tmp.XXXXXX") || { echo "deploy-error: mktemp exit $?" >&2; exit 1; }
trap 'rm -rf "$tmp"' EXIT
python3 -m venv "$tmp" || { echo "deploy-error: venv exit $?" >&2; exit 1; }
"$tmp/bin/python" -m pip install -q "unbake[dev] @ git+file://$PWD@$sha" \
  || { echo "deploy-error: pip install exit $?" >&2; exit 1; }
for file in "$tmp"/bin/*; do
  [ -f "$file" ] && [ ! -L "$file" ] && grep -qIF -- "$tmp" "$file" && sed -i "s|${tmp//./\\.}|$built|g" "$file"
done
mv -T "$tmp" "$built" || { echo "deploy-error: rename to $built exit $?" >&2; exit 1; }
trap - EXIT
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


def commands(toolkit: str) -> str:
    """The one command directory, beside the live toolkit link: the folder the rig job PATH holds."""
    return os.path.join(os.path.dirname(toolkit), COMMANDS)


def publish(toolkit: str) -> list[str]:
    """Link each command of this repository into the command directory by name, through the live toolkit link, and
    read each back: it must resolve into the live toolkit. Returns the names published."""
    folder = commands(toolkit)
    os.makedirs(folder, exist_ok=True)
    live = os.path.realpath(toolkit)
    for command in PUBLISHED:
        link = os.path.join(folder, command)
        tmp = f"{link}.{os.getpid()}.new"
        os.symlink(os.path.join(toolkit, "bin", command), tmp)
        os.replace(tmp, link)
        if os.path.commonpath([os.path.realpath(link), live]) != live:
            raise Refused(f"{link} resolves to {os.path.realpath(link)}, outside the live toolkit {live}")
    return list(PUBLISHED)


def prune(toolkit: str) -> list[str]:
    """Remove every build venv `<toolkit>.<name>` (and any leftover temporary sibling) except the live one and
    `<toolkit>.previous`, then the `<toolkit>.<SHA>` links left dangling. Returns the removed names."""
    folder, base = os.path.split(toolkit)
    keep = {os.path.realpath(toolkit), os.path.realpath(f"{toolkit}.previous")}
    entries = sorted(os.scandir(folder), key=lambda e: e.name)
    suffixes = [(e, e.name.removeprefix(f"{base}.")) for e in entries if e.name.startswith(f"{base}.")]
    removed = []
    for entry, suffix in suffixes:
        if BUILT.fullmatch(suffix) and entry.is_dir(follow_symlinks=False) and os.path.realpath(entry.path) not in keep:
            shutil.rmtree(entry.path)
            removed.append(entry.name)
    for entry, suffix in suffixes:  # after the venvs: a link whose venv is gone
        if LINK.fullmatch(suffix) and entry.is_symlink() and not os.path.exists(entry.path):
            os.unlink(entry.path)
            removed.append(entry.name)
    return removed


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
    return hashlib.sha256(f"{REQUIREMENT} @ {sha}".encode()).hexdigest()[:12]


def step_name(sha: str, toolkit: str, run: Bash) -> None:
    sys.stdout.write(f"name: {name(sha)}\n")
    sys.stdout.flush()


def step_build(sha: str, toolkit: str, run: Bash) -> None:
    code, _ = run(BUILD + SUITE, sha, toolkit, name(sha))
    if code:
        raise Refused(f"build exit {code}, links unchanged")


def step_readback(sha: str, toolkit: str, run: Bash) -> None:
    code, _ = run(READBACK, sha, toolkit, f"{toolkit}.{sha}")
    if code:
        raise Refused(f"readback exit {code}")
    sys.stdout.write(f"publish: {', '.join(publish(toolkit))} -> {commands(toolkit)}\n")
    sys.stdout.write(f"prune: {', '.join(prune(toolkit)) or 'nothing'}\n")


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
