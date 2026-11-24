# floe-lab

floe-lab is the test bench for Floe: it brings up a local storage and
catalog stack, generates deterministic data with known answers, checks Floe's query results
against those answers, and runs benchmarks, all from a clean checkout and all through `make`.
It is not a product; anything that does not help test Floe does not belong here. See
[AGENTS.md](AGENTS.md) for the engineering rules and [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

```console
$ make help
  help     List every target with its one-line description
  setup    Create .venv from uv.lock (Python, dev tools, floe-lab packages)
  lint     Run every linter and secret scan; fails on the first problem
  test     Run unit tests (everything not marked stack)
  fmt      Format code and apply safe lint fixes
```
