# Command line

```text
pyweb new NAME [--template T]        create NAME/ with app.pyweb, AGENTS.md, CLAUDE.md, static/;
                                     T = blank | counter (default) | todo | blog | auth | chat
pyweb dev FILE [--port 8000] [--host 127.0.0.1] [--no-reload]
                                     development server: recompile on save, live reload,
                                     in-browser error overlay with the failing line
pyweb check FILE                     compile + security checks; non-zero exit on problems
pyweb inspect FILE [--security]      where every name runs and why; RPC table; findings
pyweb build FILE [--out dist] [--production] [--budget PATH=SIZE ...]
                                     self-contained dist/; --production hashes and minifies;
                                     budgets fail the build when a file exceeds SIZE (e.g. 20KB)
pyweb serve [DIR] [--host 0.0.0.0] [--port 8000]
                                     production server for a built dist/
pyweb db migrate|status|rollback|new [--database URL] [--migrations DIR]
                                     [--name NAME] [--steps N] [--to VERSION]
pyweb deploy [--target docker|compose|k8s] [--out deploy] [--port 8000]
                                     write deployment files
pyweb mcp                            MCP server over stdio for AI assistants (see AI assistants & MCP)
pyweb test [PATH]                    run pytest
pyweb fmt [PATH] / pyweb lint [PATH] run ruff format / ruff check (if installed)
pyweb --version
```

`python -m pyweb.cli ...` is equivalent to `pyweb ...`.

## Exit codes

`check` and `build` exit `1` on compile or security errors and `2` on
budget breaches, so they can gate CI.
