"""`unbake objects`: compile the units of the named versions without linking and record each object's identity.

A base that does not link (overlapping layout) still compiles, and a change to compilers, flags, headers or sources
shows as a different object. The command makes every object that `build/<version>/objs.mk` lists, then writes one TSV
row per unit: unit, version, object SHA-1 and whether the host content cache served the object (`hit`) or the compiler
ran (`miss`). Two trees compared by their TSVs prove per-unit byte identity; the cache is the one the build already
uses, so a second tree of the same revision is all hits."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from abucache import stages

from unbake import build, objcache, store
from unbake import config as configuration
from unbake.contracts import Config, Finding, Json, Refusal

TABLE = Path(".unbake") / "objects.tsv"
HEADER = "unit\tversion\tsha1\tcache"
_LIST = re.compile(r"^OBJS_\S+\s*:?=")
NO_OBJECT = "-"


def _versions(config: Config, named: Sequence[str]) -> tuple[str, ...]:
    unknown = [v for v in named if v not in config.project.versions]
    if unknown:
        raise Refusal(
            Finding(
                "objects.input",
                f"unknown version {', '.join(unknown)}: the project has {', '.join(config.project.versions)}",
            )
        )
    return tuple(dict.fromkeys(named)) or tuple(config.project.versions)


def _listed(config: Config, version: str) -> list[str]:
    """The objects of one version, in the order its `objs.mk` lists them (made first when the tree has none)."""
    root = config.project.root
    path = root / configuration.root("build") / version / "objs.mk"
    if not path.is_file():
        build.make(config, [path.relative_to(root).as_posix()])
    if not path.is_file():
        raise Refusal(
            Finding(
                "objects.input",
                f"{version} has no {path.relative_to(root).as_posix()}",
                action="unbake setup --no-make",
            )
        )
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    return [word for line in text.splitlines() if _LIST.match(line) for word in _LIST.sub("", line).split()]


def _relative(out: str, root: Path) -> str:
    path = Path(out)
    return path.relative_to(root).as_posix() if path.is_absolute() and path.is_relative_to(root) else out


def _rows(root: Path, versions: Sequence[str], objects: dict[str, list[str]], served: dict[str, str]) -> list[Json]:
    """One row per unit: the SHA-1 of the object make left (NO_OBJECT when it left none) and what the cache said."""
    found = {_relative(out, root): answer for out, answer in served.items()}
    table: list[Json] = []
    for version in versions:
        prefix = f"{configuration.root('build')}/{version}/"
        for obj in objects[version]:
            path = root / obj
            sha1 = hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else NO_OBJECT
            table.append(
                {
                    "unit": obj.removeprefix(prefix),
                    "version": version,
                    "sha1": sha1,
                    "cache": found.get(obj, "none") if sha1 != NO_OBJECT else "failed",
                }
            )
    return table


def _write(path: Path, table: Sequence[Json]) -> None:
    lines = [HEADER, *("\t".join(str(row[c]) for c in HEADER.split("\t")) for row in table)]
    store.write(path, ("\n".join(lines) + "\n").encode())


def run(config: Config, params: Json) -> Json:
    """`unbake objects [--version V]... [--tsv PATH]`: make every unit's object, write the TSV."""
    root = config.project.root
    versions = _versions(config, params.get("version") or ())
    objects = {version: _listed(config, version) for version in versions}
    content = store.content_directory(config.host)
    offset = objcache.stats(content)["offset"]
    with stages.stage("objects.make"):
        ran = build.make(config, [obj for version in versions for obj in objects[version]])
    table = _rows(root, versions, objects, objcache.units(content, offset))
    target = Path(params.get("tsv") or TABLE)
    target = target if target.is_absolute() else root / target
    _write(target, table)
    failed = [f"{r['version']}:{r['unit']}" for r in table if r["cache"] == "failed"]
    if failed:
        raise Refusal(
            Finding(
                "objects.compile",
                f"{len(failed)} units made no object (make exit {ran.exit}): {', '.join(failed[:8])}",
                path=target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target),
                action="read the make log under .unbake for the compiler errors",
            )
        )
    return {
        "versions": list(versions),
        "tsv": target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target),
        "units": len(table),
        "cache": {kind: sum(1 for r in table if r["cache"] == kind) for kind in ("hit", "miss", "none")},
        "exit": ran.exit,
    }
