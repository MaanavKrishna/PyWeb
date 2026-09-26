# PyWeb

## QA harness (Track E)

Stdlib + pytest + HTTP only - no headless browser, no node/playwright:

```sh
python3 -m pytest tests/ -q -p no:cacheprovider
```

- `tests/e2e_harness.py` - compiles `.pyweb`, asserts SSR status/body/headers,
  RPC roundtrips incl. validation errors, hydration markers (`data-pw-id`,
  `pw-bind`), static-asset hashing (skips gracefully when absent), and boots
  app servers in-process on ephemeral ports.
- `tests/test_e2e.py` - harness self-tests plus per-app contracts; real
  framework integration tests skip until `pyweb` lands on main.
- Framework bugs found by QA are logged in `docs/BUGLOG.md` (not fixed here;
  routed to the framework tracks at integration).

## Reference apps

| App | Covers | Contract test |
| --- | ------ | ------------- |
| `examples/counter` | reactive binds | `TestCounterApp` |
| `examples/todo` | CRUD, binds, list rendering | `TestTodoApp` |
| `examples/blog` | SSR + SEO meta | `TestBlogApp` |
| `examples/auth` | protected route -> login redirect | `TestAuthApp` |
| `examples/chat` | SSE with polling fallback | `TestChatApp` |
| `examples/offline-notes` | offline sync contract | `TestOfflineNotesApp` |

Each app has `app.pyweb` + `meta.json` and must `pyweb build` clean and serve
SSR 200 (asserted for real once the framework lands; contract-checked until
then).

## Docs

`docs/00-quickstart.md` through `docs/07-escape-hatches.md` plus `docs/BUGLOG.md`.
Every docs code sample is extracted from a file under `examples/snippets/`
that the suite executes - zero untested snippets, enforced by
`TestDocSnippets`.

