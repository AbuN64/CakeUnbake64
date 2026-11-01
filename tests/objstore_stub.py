"""OBJSTORE for tests that make fake objects: a store that holds nothing (`get` misses, `put` is accepted)."""

import sys

sys.exit(3 if sys.argv[1] == "get" else 0)
