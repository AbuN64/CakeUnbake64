# Land

## Gate

The rebased tree must pass the repository's own lint, typing and tests before it is pushed; this is the check the card Proof runs (ci/check) plus mypy (bin/lint).

```sh
bin/dev-setup && bin/lint && ci/check
```
