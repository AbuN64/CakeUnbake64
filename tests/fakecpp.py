"""A fake cpp for tests: #if/#ifdef/#else/#endif, -D macros, INCLUDE_* stand-ins; each run is logged in cpp.log beside
the program. Unit tests answer it in process (fixture.FAKES); the program file fixture.host writes runs main()."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def expand(args: list[str], stdin: str, cwd: Path) -> str:
    """The preprocessed text of args' source (`-` reads stdin), as the fake program prints it."""
    defs = {a[2:].partition("=")[0]: a[2:].partition("=")[2] or "1" for a in args if a.startswith("-D")}
    text = stdin if "-" in args else (cwd / args[-1]).read_text()

    def truth(expr: str) -> bool:
        expr = re.sub(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", lambda m: str(int((m[1] or m[2]) in defs)), expr)
        expr = re.sub(r"[A-Za-z_]\w*", lambda m: defs.get(m[0], "0"), expr)
        return bool(eval(expr.replace("&&", " and ").replace("||", " or ").replace("!", " not ")))

    out: list[str] = []
    stack: list[tuple[bool, bool]] = []
    for line in text.splitlines():
        word = re.match(r"\s*#\s*(\w+)\s*(.*)", line)
        if word:
            kind, rest = word[1], word[2]
            live = all(a for a, _ in stack)
            if kind in ("if", "ifdef", "ifndef"):
                value = truth(rest) if kind == "if" else (rest.strip() in defs) == (kind == "ifdef")
                stack.append((value, value))
            elif kind == "elif":
                _, taken = stack.pop()
                value = not taken and truth(rest)
                stack.append((value, taken or value))
            elif kind == "else":
                _, taken = stack.pop()
                stack.append((not taken, True))
            elif kind == "endif":
                stack.pop()
            elif kind == "define" and live:
                parts = rest.split(None, 1)
                defs[parts[0]] = parts[1] if len(parts) > 1 else "1"
            continue
        if not all(a for a, _ in stack):
            continue
        stand = re.match(r"\s*INCLUDE_(ASM|DATA|RODATA)\(\s*\"?(\w+)\"?\s*,\s*(\w+)\s*\)\s*;?\s*$", line)
        if stand and "include_asm.h" in text:  # like the real header: without it the call stays as written
            folder = "nonmatchings" if stand[1] == "ASM" else "data"
            section = {"ASM": ".text", "DATA": ".data", "RODATA": ".rodata"}[stand[1]]
            out.append(f'__asm__(".section {section}\\n.include \\"{folder}/{stand[2]}/{stand[3]}.s\\"");')
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def run(program: Path, args: list[str], stdin: str, cwd: Path) -> str:
    """One run of the fake at `program`: logged beside it, then expanded."""
    with (program.parent / "cpp.log").open("a") as log:
        log.write(" ".join(a for a in args if a.startswith("-D")) + "\n")
    return expand(args, stdin, cwd)


def main() -> None:
    args = sys.argv[1:]
    stdin = sys.stdin.read() if "-" in args else ""
    sys.stdout.write(run(Path(sys.argv[0]).absolute(), args, stdin, Path.cwd()))
