"""The host file's tools table, kept current by `unbake setup --rewrite-host`.

A tool the build renamed leaves its old key in a host file written before; every unbake command refuses such a file
(`config.retired_refusal`) naming the one command that rewrites it: the retired key is dropped and the current tool is
written as a path to its pinned release, fetched into the host's toolchains root the way setup installs a toolchain."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from unbake import build, store
from unbake import config as configuration
from unbake.contracts import Finding, Json, Refusal

_SECTION = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _tool_line(name: str, path: Path) -> str:
    return f'{name} = "{path}"\n'


def _rewritten(text: str, drop: list[str], tool: str, binary: Path | None) -> str:
    """`text` without the dropped keys of its [tools] table and with `tool = binary` set there (a key that stays is
    kept in place; a new one goes after the table's last key)."""
    out: list[str] = []
    in_tools, last, replaced = False, -1, binary is None
    for line in text.splitlines(keepends=True):
        if section := _SECTION.match(line):
            in_tools = section[1].strip() == "tools"
        elif in_tools and (key := _KEY.match(line)):
            if key[1] in drop:
                continue
            if key[1] == tool and binary is not None:
                line, replaced = _tool_line(tool, binary), True
            last = len(out)
        out.append(line)
    if not replaced and binary is not None:
        if last < 0:
            raise Refusal(Finding("config.schema", "the host file has no [tools] table", path="host:tools"))
        if not out[last].endswith("\n"):
            out[last] += "\n"
        out.insert(last + 1, _tool_line(tool, binary))
    return "".join(out)


def rewrite(path: Path) -> Json:
    """Rewrite the tools table of the host file `path`: retired keys dropped, abumas present and installed. Nothing is
    written unless the result loads as a host file; a file that is already current is left alone."""
    try:
        text = path.read_text(encoding="utf-8")
        document = tomllib.loads(text)
    except FileNotFoundError as error:
        raise Refusal(Finding("config.missing", f"{path} does not exist", path=str(path))) from error
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise Refusal(Finding("config.schema", f"not valid TOML: {error}", path="host:<root>")) from error
    root = document.get("toolchains", {}).get("root")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise Refusal(Finding("config.file", "toolchains.root is not an absolute path", path="host:toolchains.root"))
    tool = "abumas"
    held = document.get("tools", {}).get(tool)
    current = Path(held) if isinstance(held, str) else None
    usable = current is not None and current.is_absolute() and current.is_file() and os.access(current, os.X_OK)
    drop = configuration.retired_tools(document)
    binary = current if usable else build.install_pinned(Path(root), tool)
    wanted = _rewritten(text, drop, tool, None if usable else binary)
    result: Json = {"host": str(path), "removed": drop, "tool": tool, "binary": str(binary), "written": wanted != text}
    if wanted == text:
        return result
    staged = path.with_name(f".{path.name}.rewrite")
    staged.write_text(wanted, encoding="utf-8")
    try:
        configuration.load_host(staged)
    finally:
        staged.unlink(missing_ok=True)
    store.write(path, wanted.encode("utf-8"))
    return result
