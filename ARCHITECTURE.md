# PyWeb Architecture

## Track D — developer tooling + performance

| area | module | notes |
| --- | --- | --- |
| CLI | `pyweb/cli/__init__.py` | `new/dev/build/inspect/npm` + `test/check/fmt/lint/db/deploy` stubs |
| app graph | `pyweb/_graph.py` | regex + AST scan; consumes `placement.decide()` read-only |
| dev server | `pyweb/dev.py` | stdlib HTTP + polling watcher + SSE; overlay + sourcemap endpoints |
| LSP | `pyweb/lsp.py` | pure completions + `CompileError`→diagnostic mapping |
| observability | `pyweb/observability.py` | `Tracer`/`Timer`/`timed`/`measure` |
| npm interop | `pyweb/npm.py` | `.d.ts` interfaces → dataclasses + validators |
| benchmarks | `benchmarks/run.py`, `benchmarks/BASELINE.md` | SSR/TTFB/suite; static <2KB, runtime <8KB |

Design constraints: stdlib-only at runtime (no watchdog, no LSP server dep);
placement is consumed, never modified; sourcemap mapping degrades to raw
stacks when pipeline artifacts are absent. See `docs/DEVTOOLS.md` for usage.
