"""workers: unbake's seat on abucache.pool, with the pool itself mocked."""

from __future__ import annotations

from abucache import pool, slots
from test_effort import _config

from unbake import effort, workers


def test_bind_configures_the_pool_and_the_slots_from_the_host(tmp_path, monkeypatch):
    config = _config(tmp_path)
    seen: dict[str, object] = {}
    monkeypatch.setattr(pool, "configure", lambda runtime, **kw: seen.update(pool=(runtime, kw)))
    monkeypatch.setattr(slots, "configure", lambda runtime, **kw: seen.update(slots=(runtime, kw)))
    monkeypatch.setattr(effort, "priority", True)
    workers.bind(config)
    runtime, kw = seen["pool"]
    assert (runtime.cores, runtime.workers) == (config.host.cores, config.host.slots)
    assert kw == {"spill_dir": config.project.root / ".unbake/pool", "identity_package": "unbake"}
    assert seen["slots"] == (runtime, {"priority": True})


def test_a_snapshot_is_shared_by_its_project_and_digest_and_released_by_its_digest():
    from types import SimpleNamespace

    from unbake.contracts import Snapshot

    spec = next(s for s in pool._shared if s.cls is Snapshot)
    first = SimpleNamespace(config=SimpleNamespace(project=SimpleNamespace(root="/p")), digest="a")
    second = SimpleNamespace(config=SimpleNamespace(project=SimpleNamespace(root="/p")), digest="b")
    assert spec.key(first) != spec.key(second) and spec.tag(first) == "a"
