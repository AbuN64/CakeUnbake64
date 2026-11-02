"""unbake's seat on the one worker pool (abucache.pool) and the host slots (abucache.slots): what a worker must hold
that only unbake knows. The pool, its slots, its spill files and its keyed results are abucache's; this module
registers the seam once at import and `bind` configures it once per command."""

from __future__ import annotations

import os

from abucache import pool, slots

from unbake import effort, hostruntime, journal, store
from unbake.contracts import Config, Refusal, Snapshot, sha256

KEEP = ("declarations", "words", "spelled", "typed")  # memo kinds keyed by content alone: valid for every overlay
WARM = ("catalog", "spellings", "authority", "spellers")  # content-keyed indexes the parent builds for a round's tree:
# a spill file carries them, so warm workers read them instead of building them each (forked workers inherited them)


def _key(snapshot: Snapshot) -> str:
    return sha256((str(snapshot.config.project.root) + snapshot.digest).encode())


def _drop_inherited() -> None:
    """A forked process lets go of the locks it inherited: they are the parent's, and the journal's landing lock is
    taken again by the worker's own readers and writers."""
    store.drop_inherited()
    journal.drop_inherited()


pool.register_shared(Snapshot, key=_key, tag=lambda snapshot: snapshot.digest)
pool.register_context("journal", journal.holding, journal.adopt)  # a job reads under the landing lock its parent holds
pool.register_passthrough(Refusal)  # workers return Refusals across processes
pool.register_warm(WARM, KEEP)
os.register_at_fork(after_in_child=_drop_inherited)


def bind(config: Config) -> None:
    """Gives the pool and the slots this command's host facts: the pool's spill files live under the project."""
    runtime = hostruntime.runtime(config.host)
    pool.configure(runtime, spill_dir=config.project.root / ".unbake/pool", identity_package="unbake")
    slots.configure(runtime, priority=effort.priority)
