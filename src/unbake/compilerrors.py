"""The first compiler error lines of each unit a make run failed to build, read from make's own log.

A failed make names its units only as `make: *** [Makefile:N: build/<v>/<source>.o] Error K`; the compiler's diagnostics
sit elsewhere in the same log, on `<object stem>.i:<line>: error: ...` lines. One reader joins the two, so every refusal
that reports a failed build (setup.make, setup.splat, unify.baseline, unify.rebuild) shows why each unit failed."""

from __future__ import annotations

import re
from collections.abc import Iterable

ERROR_LINES = 3  # compiler error lines kept per failing object
NO_ERROR = "no compiler error in the log"
FAILED = re.compile(r"\*\*\* \[(?:[^\]\s]+: )?([^\]\s]+\.o)\]")
# a unit whose preprocess stamp failed (a header the rewrites left out) builds no object either: its stamp is the unit's
STAMP = re.compile(r"\*\*\* \[(?:[^\]\s]+: )?([^\]\s]+)\.i\.ok\]")
ERROR = re.compile(r"^(.+?)\.i:\d+:(?:\d+:)? (?!warning)(.*)$", re.M)


def first_errors(log: str, failed: Iterable[str]) -> dict[str, str]:
    """The first compiler error lines of each failed object, from one pass over the log (a scan of the log per failed
    object is failed x log size of serial work after make returned)."""
    wanted = {path[:-2]: path for path in failed}
    lines: dict[str, list[str]] = {path: [] for path in wanted.values()}
    for match in ERROR.finditer(log):
        path = wanted.get(match[1])
        if path is not None and len(lines[path]) < ERROR_LINES:
            lines[path].append(match[2])
    return {path: " | ".join(first) if first else NO_ERROR for path, first in lines.items()}


def stamp_errors(log: str, stamps: Iterable[str]) -> dict[str, str]:
    """The first error line of each object whose preprocess stamp failed: it names the unit's source, not its `.i`."""
    out: dict[str, str] = {}
    for path in stamps:
        source = path.split("/", 2)[2][:-2]
        found = re.search(rf"^{re.escape(source)}:\d+:(?:\d+:)? ((?:fatal )?error.*)$", log, re.M)
        out[path] = found[1] if found else "its preprocess failed"
    return out


def failures(log: str) -> dict[str, str]:
    """Every object the log's make lines name as failed, in the order they failed -> its first compiler error lines."""
    named: dict[str, bool] = {}  # object -> failed at its preprocess stamp
    for line in log.splitlines():
        if (hit := FAILED.search(line)) is not None:
            named.setdefault(hit[1], False)
        elif (hit := STAMP.search(line)) is not None:
            named.setdefault(f"{hit[1]}.o", True)
    errors = first_errors(log, [p for p, stamp in named.items() if not stamp])
    errors.update(stamp_errors(log, [p for p, stamp in named.items() if stamp]))
    return {path: errors[path] for path in named}


def reason(log: str, tail: int = 20) -> str:
    """A failed make's refusal text: one line per failed object (its path and first compiler errors), any other `***`
    line as make wrote it; a log with no `***` line ends with its last `tail` lines."""
    errors = failures(log)
    lines: list[str] = []
    for line in log.splitlines():
        if "***" not in line:
            continue
        hit = FAILED.search(line) or STAMP.search(line)
        path = None if hit is None else hit[1] if hit.re is FAILED else f"{hit[1]}.o"
        if path is None:
            lines.append(line)
        elif path in errors:
            lines.append(f"{path}: {errors.pop(path)}")  # a unit is told once however many lines make wrote for it
    return "\n".join(lines or log.splitlines()[-tail:])
