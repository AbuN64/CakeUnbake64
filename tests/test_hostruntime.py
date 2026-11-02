"""hostruntime: a host's runtime.toml follows its host config and abucache accepts it."""

from __future__ import annotations

from abucache import runtime

from unbake import config, hostruntime


def test_the_runtime_file_takes_the_host_configs_resolved_values_and_abucache_loads_it(host_file, tmp_path):
    host = config.load_host(host_file)
    out = tmp_path / "abu" / "runtime.toml"
    assert hostruntime.main(["--host", str(host_file), "--out", str(out)]) == 0
    loaded = runtime.load(out)
    assert (loaded.cores, loaded.workers) == (host.cores, host.slots)
    assert (loaded.worker_memory, loaded.parent_memory) == (host.memory_worker_bytes, host.memory_parent_bytes)
    assert (loaded.cache_dir, loaded.cache_bytes) == (host.cache_dir, host.cache_max_bytes)
    assert loaded.feed == host.cache_dir / "events.jsonl" and dict(loaded.capacities) == {}


def test_without_out_the_document_goes_to_stdout(host_file, capsys):
    assert hostruntime.main(["--host", str(host_file)]) == 0
    assert capsys.readouterr().out == hostruntime.runtime_toml(config.load_host(host_file))


def test_the_resolved_runtime_equals_the_file_abucache_loads(host_file, tmp_path):
    host = config.load_host(host_file)
    out = tmp_path / "runtime.toml"
    assert hostruntime.main(["--host", str(host_file), "--out", str(out)]) == 0
    assert hostruntime.runtime(host) == runtime.load(out)
