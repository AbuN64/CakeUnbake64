from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Json = Any
WAIT_NOTE: float
finding_to_dict: Callable[[Any], dict[str, Any]]

@dataclass(frozen=True)
class Budgets:
    serial_seconds: float
    serial_cores: float
    pool_fill: float
    pool_fanout: float

@dataclass(frozen=True)
class StageRecord:
    invocation: str
    span: str
    parent: str | None
    path: tuple[str, ...]
    kind: str
    status: str
    execution: str
    start_ns: int
    wall_seconds: float
    parent_cpu_seconds: float
    worker_cpu_seconds: float
    native_cpu_seconds: float
    cores: float
    items: int
    jobs: int
    workers_used: int
    workers_admitted: int
    parent_rss_peak_bytes: int
    worker_rss_peak_bytes: int
    memory_limits: Mapping[str, str]
    memory_limit_values: Mapping[str, int]
    cache: Mapping[str, tuple[int, int]]
    findings: tuple[dict[str, Any], ...]
    wait_seconds: float
    dispatch_seconds: float
    busy_seconds: float
    spans: int
    slot_seconds: float
    slot_wait_seconds: float
    lock_wait_seconds: float

def configure(
    runtime: Any = None,
    *,
    budgets: Budgets | None = None,
    budget_origins: Mapping[str, Any] | None = None,
    memory_limits: Mapping[str, str] | None = None,
    memory_limit_values: Mapping[str, int] | None = None,
    parent: int | None = None,
    package: str = "",
    project: str = "",
    wait_log: Path | None = None,
) -> None: ...
def register_refusal(*types: type[BaseException]) -> None: ...
def command(tool: str, name: str, argv: Sequence[str], sink: Path) -> AbstractContextManager[None]: ...
def stage(name: str) -> AbstractContextManager[Any]: ...
def listen(callback: Callable[[str, Json], None]) -> None: ...
def say(text: str) -> None: ...
def progress(name: str, done: int, total: int) -> None: ...
def closed() -> tuple[StageRecord, ...]: ...
def waited(seconds: float, what: str = "lock", detail: str = "") -> None: ...
def expected(path: Sequence[str]) -> float | None: ...
def record_native(name: str, completed: Any, start_ns: int) -> None: ...
def record_pool(
    name: str,
    items: int,
    jobs: int,
    admitted: int,
    start_ns: int,
    envelopes: Sequence[Json],
    dispatch_seconds: float = 0.0,
    end_ns: int | None = None,
    slot_seconds: float = 0.0,
) -> None: ...
def fold(shipped: Sequence[tuple[StageRecord, bool]]) -> list[tuple[StageRecord, bool]]: ...
def count(kind: str, hit: bool) -> None: ...
def counters() -> dict[str, tuple[int, int]]: ...
def cache_total(stages: Json) -> Json: ...
def capture() -> AbstractContextManager[list[StageRecord]]: ...
def tree() -> Json: ...
def invocation() -> str: ...
