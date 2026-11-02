from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Runtime:
    cores: int
    workers: int
    worker_memory: int
    parent_memory: int
    reserve_memory: int
    capacities: Mapping[str, int]
    cache_dir: Path
    cache_bytes: int
    cache_keep_seconds: int
    stage_log_bytes: int
    feed: Path
    slow_seconds: int
    overrun_percent: int
    digest: str
