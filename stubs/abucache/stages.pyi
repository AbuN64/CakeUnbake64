from pathlib import Path
from typing import Any

def configure(
    runtime: Any = None,
    *,
    parent: int | None = None,
    package: str = "",
    project: str = "",
    wait_log: Path | None = None,
) -> None: ...
