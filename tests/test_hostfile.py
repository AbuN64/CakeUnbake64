"""hostfile: a host file carrying a retired tool key is refused naming the command that rewrites it, and that command
(`unbake setup --rewrite-host`) writes the current tools table."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import fixture
import pytest

from unbake import build, cli, hostfile
from unbake import config as configuration
from unbake.contracts import Finding, Refusal, sha256


def _retired_host(tmp_path: Path, *, keep_abumas: bool = False) -> Path:
    """A host file as it was written before the rename: `n64link = <path>` and no `abumas`."""
    path = fixture.host(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    old = tmp_path / "bin" / "n64link"
    old.write_bytes((tmp_path / "bin" / "abumas").read_bytes())
    old.chmod(0o755)
    out = []
    for line in lines:
        if line.startswith("abumas = "):
            line = f'n64link = "{old}"\n' + (line if keep_abumas else "")
        out.append(line)
    path.write_text("".join(out))
    return path


def test_a_retired_tool_key_is_refused_naming_the_command_that_rewrites_it(tmp_path: Path) -> None:
    path = _retired_host(tmp_path)
    with pytest.raises(Refusal) as caught:
        configuration.load_host(path)
    (finding,) = caught.value.findings
    assert finding.key == "config.retired_tool" and finding.path == "host:tools.n64link"
    assert "unbake setup --rewrite-host" in finding.reason and "abumas" in finding.reason
    assert finding.action == f"unbake setup --rewrite-host --host {path}"


def test_the_rewrite_command_drops_the_retired_key_and_writes_the_installed_abumas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _retired_host(tmp_path)
    installed = tmp_path / "toolchains" / "abumas" / "abumas"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"#!/bin/sh\n")
    installed.chmod(0o755)
    seen = []
    monkeypatch.setattr(build, "install_pinned", lambda root, name: seen.append((root, name)) or installed)
    result = hostfile.rewrite(path)
    assert seen == [(tmp_path / "toolchains", "abumas")]
    assert result["removed"] == ["n64link"] and result["written"] is True
    assert "n64link" not in path.read_text()
    host = configuration.load_host(path)
    assert host.tools["abumas"] == installed
    assert hostfile.rewrite(path)["written"] is False and len(seen) == 1  # a current file is left alone


def test_a_usable_abumas_is_kept_and_only_the_retired_key_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _retired_host(tmp_path, keep_abumas=True)
    monkeypatch.setattr(build, "install_pinned", lambda root, name: pytest.fail("abumas is already installed"))
    assert hostfile.rewrite(path)["removed"] == ["n64link"]
    assert configuration.load_host(path).tools["abumas"] == tmp_path / "bin" / "abumas"


def test_a_rewrite_that_would_not_load_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _retired_host(tmp_path)
    path.write_text(path.read_text().replace("schema = 2", "schema = 2\nbogus = 1"))
    before = path.read_text()
    installed = tmp_path / "bin" / "abumas"
    monkeypatch.setattr(build, "install_pinned", lambda root, name: installed)
    with pytest.raises(Refusal):
        hostfile.rewrite(path)
    assert path.read_text() == before and not list(tmp_path.glob(".*.rewrite"))


def test_setup_names_the_rewrite_flag_and_it_runs_before_the_host_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(hostfile, "rewrite", lambda path: order.append("rewrite") or {"host": str(path)})

    def load(root: Path, host: Path) -> None:
        order.append("load")
        raise Refusal(Finding("config.missing", "stop here"))

    monkeypatch.setattr(cli.config, "load", load)
    monkeypatch.setattr(cli, "_emit", lambda *args, **kw: 1)
    cli._run("setup", {"host": tmp_path / "host.toml", "project": tmp_path, "rewrite_host": True}, [])
    assert order == ["rewrite", "load"]


def test_a_pinned_release_is_fetched_checked_and_unpacked_into_the_toolchains_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"#!/bin/sh\necho abumas\n"
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo("abumas-v1/abumas")
        member.size = len(body)
        tar.addfile(member, io.BytesIO(body))
    data = archive.getvalue()
    row = {
        "host": "abumas",
        "binary": "abumas",
        "download": {
            "url": "https://example.invalid/a.tar.gz",
            "sha256": sha256(data),
            "files": ["abumas-v1/abumas"],
            "strip": 1,
        },
        "pins": {"abumas": sha256(body)},
    }
    monkeypatch.setattr(build.configuration, "load_resource", lambda name: {"make": {"tools": {"ABUMAS": row}}})
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda url: io.BytesIO(data))
    path = build.install_pinned(tmp_path, "abumas")
    assert path == tmp_path / "abumas" / "abumas" and path.read_bytes() == body and path.stat().st_mode & 0o111
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda url: pytest.fail("installed and pinned: no fetch"))
    assert build.install_pinned(tmp_path, "abumas") == path
    with pytest.raises(Refusal) as unpinned:
        build.install_pinned(tmp_path, "git")
    assert unpinned.value.findings[0].key == "toolchain.pin"
