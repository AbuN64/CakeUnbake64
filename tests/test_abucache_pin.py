"""Setup fetches the abucache commit unbake pins from the host-configured URL when it is not installed."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fixture
import pytest

from unbake import adapters
from unbake.contracts import Config, Host, Refusal

REAL_GIT = "the pin is fetched from a real git origin into a real clone"

_SHA = "e00b0f706db980da04f137118e9328b6c24b9529"


@pytest.fixture
def adapter_config(tmp_path):
    git = Path(shutil.which("git") or "git")
    host = Host(
        2, 2, 1024, 1024, tmp_path / "cache", 1024, tmp_path / "chains", {"git": git}, None, 2, 2, 0.8, 2,
        ("test", "test@invalid"), {}, "host",
    )  # fmt: skip
    project = SimpleNamespace(root=tmp_path, digest="project")
    return Config(project, host, "config")  # type: ignore[arg-type]


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def abucache_origin(tmp_path):
    """A repository with two commits standing in for the configured abucache URL; the pin is the first."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    for number in ("1", "2"):
        (origin / "f").write_text(number)
        _git("add", "f", cwd=origin)
        _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", number, cwd=origin)
    return origin, _git("rev-parse", "HEAD~1", cwd=origin)


def _fetching_config(adapter_config, origin: Path, clone: Path) -> Config:
    host = replace(adapter_config.host, abucache_url=str(origin), abucache_clone=clone)
    return replace(adapter_config, host=host)


@pytest.mark.integration(REAL_GIT)
def test_a_pin_absent_from_the_clone_is_fetched_from_the_configured_url_then_installed(
    monkeypatch, tmp_path, adapter_config, abucache_origin
):
    origin, pin = abucache_origin
    clone = tmp_path / "clone.git"
    _git("init", "-q", "--bare", str(clone), cwd=tmp_path)
    _git("remote", "add", "origin", "https://stale.invalid/old.git", cwd=clone)  # a stale origin is never used
    config = _fetching_config(adapter_config, origin, clone)
    installed = ["0" * 40]
    monkeypatch.setattr(adapters, "_pin", lambda: pin)
    monkeypatch.setattr(adapters, "_installed", lambda: installed[0])
    pip_calls: list[list[str]] = []

    real = adapters._native

    def native(config, name, argv):
        if name != "pip install":
            return real(config, name, argv)
        pip_calls.append([str(a) for a in argv])
        installed[0] = pin
        return fixture.native_result()

    monkeypatch.setattr(adapters, "_native", native)
    execv = Mock()
    monkeypatch.setattr(adapters.os, "execv", execv)
    adapters.ensure_abucache(config)
    assert _git("rev-parse", f"refs/heads/pin-{pin}", cwd=clone) == pin
    assert _git("rev-parse", "HEAD", cwd=clone) == pin  # pip clones the clone: its HEAD holds the pin
    assert pip_calls[0][-1] == f"abucache @ git+{clone.as_uri()}@{pin}"
    execv.assert_called_once()


@pytest.mark.integration(REAL_GIT)
def test_a_pin_already_in_the_clone_is_not_fetched(monkeypatch, tmp_path, adapter_config, abucache_origin):
    origin, pin = abucache_origin
    clone = tmp_path / "clone.git"
    config = _fetching_config(adapter_config, origin, clone)
    assert adapters._fetch_abucache(config, pin) == clone
    seen: list[str] = []
    real = adapters._native
    monkeypatch.setattr(adapters, "_native", lambda c, name, argv: seen.append(name) or real(c, name, argv))
    adapters._fetch_abucache(config, pin)
    assert seen == ["git rev-parse", "git symbolic-ref"]


def test_the_installed_pin_changes_nothing(monkeypatch, adapter_config):
    monkeypatch.setattr(adapters, "_pin", lambda: _SHA)
    monkeypatch.setattr(adapters, "_installed", lambda: _SHA)
    monkeypatch.setattr(adapters, "_native", Mock(side_effect=AssertionError("nothing runs")))
    adapters.ensure_abucache(adapter_config)


@pytest.mark.integration(REAL_GIT)
def test_a_pin_the_url_cannot_give_refuses_naming_the_commit_and_the_url(
    monkeypatch, tmp_path, adapter_config, abucache_origin
):
    origin, _ = abucache_origin
    missing = "1" * 40
    config = _fetching_config(adapter_config, origin, tmp_path / "clone.git")
    monkeypatch.setattr(adapters, "_pin", lambda: missing)
    monkeypatch.setattr(adapters, "_installed", lambda: "0" * 40)
    with pytest.raises(Refusal) as caught:
        adapters.ensure_abucache(config)
    (finding,) = caught.value.findings
    assert finding.key == "tool.version"
    assert f"abucache commit {missing} not fetched from {origin}" in finding.reason


def test_a_host_without_an_abucache_url_refuses_naming_the_commit(monkeypatch, adapter_config):
    monkeypatch.setattr(adapters, "_pin", lambda: _SHA)
    monkeypatch.setattr(adapters, "_installed", lambda: "0" * 40)
    with pytest.raises(Refusal) as caught:
        adapters.ensure_abucache(adapter_config)
    assert _SHA in caught.value.findings[0].reason
    assert "[abucache]" in caught.value.findings[0].reason
