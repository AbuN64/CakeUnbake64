from dataclasses import dataclass

@dataclass(frozen=True)
class Completed:
    argv: tuple[str, ...]
    cwd: str
    exit: int | None
    signal: int | None
    stdout: bytes
    stderr: bytes
    wall: float
    cpu: float
    max_rss: int
    start_ns: int = 0
