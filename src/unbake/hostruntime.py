"""The host's runtime.toml, rendered from its unbake host config: `python -m unbake.hostruntime [--host H] [--out P]`.

abucache reads one runtime file per host (`ABU_RUNTIME`, else `~/.config/abu/runtime.toml`); the cores, workers,
memory, cache directory and cache size are the host config's resolved values, so a host's site facts live only in its
operator file and the runtime file follows them. Without `--out` the document goes to stdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

from abucache import keys
from abucache import runtime as runtime_file

from unbake import config
from unbake.contracts import Host

CACHE_KEEP_SECONDS = 14 * 86400  # a cache root without git history keeps its entries live this long
STAGE_LOG_BYTES = 1 << 20
SLOW_SECONDS = 60
OVERRUN_PERCENT = 150


def runtime_toml(host: Host) -> str:
    """The runtime document of `host`: every key abucache.runtime requires, its host facts taken from the host config
    (`host.slots` is the worker budget the commands divide) and the feed beside the cache."""
    feed = host.cache_dir / "events.jsonl"
    return (
        "[resources]\n"
        f"cores = {host.cores}\n"
        f"workers = {host.slots}\n"
        f"worker_memory = {host.memory_worker_bytes}\n"
        f"parent_memory = {host.memory_parent_bytes}\n"
        f"reserve_memory = {host.memory_parent_bytes}\n\n"  # the parent's share is also kept free
        "[cache]\n"
        f"dir = {json.dumps(str(host.cache_dir))}\n"
        f"bytes = {host.cache_max_bytes}\n"
        f"keep_seconds = {CACHE_KEEP_SECONDS}\n\n"
        f"[stages]\nlog_bytes = {STAGE_LOG_BYTES}\n\n"
        f"[events]\nfeed = {json.dumps(str(feed))}\n"
        f"slow_seconds = {SLOW_SECONDS}\noverrun_percent = {OVERRUN_PERCENT}\n\n"
        "[capacities]\n"
    )


def runtime(host: Host) -> runtime_file.Runtime:
    """The same document as `runtime_toml`, resolved: what abucache.pool and abucache.slots are configured with."""
    values: dict[str, Any] = {
        "cores": host.cores,
        "workers": host.slots,
        "worker_memory": host.memory_worker_bytes,
        "parent_memory": host.memory_parent_bytes,
        "reserve_memory": host.memory_parent_bytes,
        "capacities": MappingProxyType({}),
        "cache_dir": host.cache_dir,
        "cache_bytes": host.cache_max_bytes,
        "cache_keep_seconds": CACHE_KEEP_SECONDS,
        "stage_log_bytes": STAGE_LOG_BYTES,
        "feed": host.cache_dir / "events.jsonl",
        "slow_seconds": SLOW_SECONDS,
        "overrun_percent": OVERRUN_PERCENT,
    }
    return runtime_file.Runtime(**values, digest=keys.digest(values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unbake.hostruntime", description=__doc__)
    parser.add_argument("--host", type=Path, help="host config, default ~/.config/unbake/unbake.toml")
    parser.add_argument("--out", type=Path, help="write the runtime file here instead of stdout")
    args = parser.parse_args(argv)
    path = args.host or config.default_host()
    if path is None:
        parser.exit(2, f"no host config: {config.NO_HOST}\n")
    text = runtime_toml(config.load_host(path))
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
