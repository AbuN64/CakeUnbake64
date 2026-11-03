"""unbake's seat on the stage tree: the sink, the bound host config, the hold beat and the niceness. The tree is
tested in abucache."""

import json
from types import SimpleNamespace

import pytest
from abucache import stages

from unbake import effort
from unbake.contracts import Config, Finding, Host, Origin, Refusal


def _config(tmp_path):
    keys = ("resources.memory_parent_bytes", "resources.memory_worker_bytes", "budgets.serial_seconds")
    origins = {key: Origin(key, "/host.toml", "0" * 64) for key in keys}
    memory = 1 << 30
    host = Host(
        2, 2, memory, memory, tmp_path / "cache", memory, tmp_path / "tools", {}, None, 2, 2, 0.8, 2,
        ("test", "test@invalid"), origins, "host",
    )  # fmt: skip
    project = SimpleNamespace(
        root=tmp_path, name="fixture", title="Fixture", toolchain="test", versions=(), version={}, cppflags=(),
        cflags=(), asflags=(), files={}, libraries=(), names_from="", digest="project",
    )  # fmt: skip
    return Config(project, host, "config")  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _listeners(monkeypatch):
    monkeypatch.setattr(stages, "_listeners", list(stages._listeners))


def test_a_command_logs_under_dot_unbake_of_its_project(tmp_path):
    with effort.command("check", [], tmp_path):
        effort.bind(_config(tmp_path))
        with stages.stage("work"):
            pass
    rows = [json.loads(line) for line in effort.sink(tmp_path).read_text().splitlines()]
    assert effort.sink(tmp_path) == tmp_path / ".unbake" / "stages.jsonl"
    assert {r["span"] for r in rows} <= {r.span for r in stages.closed()}
    assert rows[-1]["kind"] == "command"


def test_bind_hands_the_host_limits_and_budgets_to_the_tree(tmp_path):
    with effort.command("check", [], tmp_path):
        effort.bind(_config(tmp_path))
    root = stages.closed()[-1]
    assert root.memory_limits == {
        "resources.memory_parent_bytes": "/host.toml",
        "resources.memory_worker_bytes": "/host.toml",
    }
    assert root.memory_limit_values["resources.memory_parent_bytes"] == 1 << 30


def test_a_refusal_ends_its_span_refused_with_its_findings_as_rows(tmp_path):
    with pytest.raises(Refusal), effort.command("check", [], tmp_path), stages.stage("work"):
        raise Refusal(Finding("effort.log", "no"))
    record = next(r for r in stages.closed() if r.path[-1] == "work")
    assert record.status == "refused"
    assert [f["key"] for f in record.findings] == ["effort.log"]


def test_a_command_running_past_the_beat_says_its_stage_and_cpu(monkeypatch, tmp_path):
    """No silent hold: a long command prints its wall, process-tree CPU and the innermost open stage every beat."""
    import threading

    said = []
    beat = threading.Event()

    def listen(event, body):
        if event == "say":
            said.append(body["text"])
            if body["text"].startswith("hold:") and "now in submit > publish.build" in body["text"]:
                beat.set()  # a beat said before the stage opened (only "now in submit") is not the signal

    monkeypatch.setattr(effort, "HOLD_BEAT", 0.05)
    stages.listen(listen)
    with effort.command("submit", ["submit"], tmp_path), stages.stage("publish.build"):
        assert beat.wait(60)  # the beat naming the stage is the signal; 60 s only bounds a beat that never comes
    assert any("hold: submit runs" in line and "CPU-s, now in submit > publish.build" in line for line in said)


def test_a_beat_before_the_stage_opens_does_not_end_the_wait(monkeypatch, tmp_path):
    """The old failure order, forced without timing: a beat said outside the stage, then the stage opens, then its beat."""
    import threading

    said = []
    outside, inside = threading.Event(), threading.Event()

    def listen(event, body):
        if event == "say" and body["text"].startswith("hold:"):
            said.append(body["text"])
            if body["text"].endswith("now in submit"):
                outside.set()
            elif "now in submit > publish.build" in body["text"]:
                inside.set()

    monkeypatch.setattr(effort, "HOLD_BEAT", 0.05)
    stages.listen(listen)
    with effort.command("submit", ["submit"], tmp_path):
        assert outside.wait(60)  # a hold line said while only the command span is open
        assert not inside.is_set()
        with stages.stage("publish.build"):
            assert inside.wait(60)  # every later beat repeats the line, now naming the stage
    assert any(line.endswith("now in submit") for line in said)
    assert any("CPU-s, now in submit > publish.build" in line for line in said)


def test_only_the_ship_lock_holder_keeps_normal_priority(monkeypatch):
    """One rule: a command without `--priority` runs nice (inherited by its workers); the holder is not niced."""
    calls = []
    level = [0]

    def nice(step):
        level[0] += step
        calls.append(step)
        return level[0]

    monkeypatch.setattr(effort.os, "nice", nice)
    monkeypatch.delenv("UNBAKE_NICE", raising=False)
    monkeypatch.setattr(effort, "priority", True)
    assert effort.yield_cpu() == 0 and calls == [0]
    monkeypatch.setattr(effort, "priority", False)
    assert effort.yield_cpu() == effort.NICE
    assert effort.yield_cpu() == effort.NICE and calls[-1] == 0  # never lowers it, never nices twice
    monkeypatch.setenv("UNBAKE_NICE", "0")
    assert effort.yield_cpu() == effort.NICE and calls[-1] == 0  # 0 keeps whatever it is
