# 10 - Production build

> Source: `pyweb/build.py` (tested in `tests/test_browser_build.py`).

```bash
python -m pyweb.cli build app.pyweb --out dist --production
```

## Layout

```text
dist/
  static/
    runtime.<hash>.js    shared runtime, cached across pages
    <page>.<hash>.js     per-route split + minified + source map
    <page>.<hash>.css    extracted page styles
  server/
    <page>.html          SSR shell referencing hashed assets
  manifest.json          routes, rpc, hashes, byte sizes
  deploy/Dockerfile      standalone multi-stage image
```

## Why this shape

- **Content hashes** = immutable caching; a deploy only invalidates
  changed pages.
- **Per-route splits** = a blog post never ships chat-room code.
- **Minify without a dep**: emitted JS is machine-generated (no exotic
  syntax), so comment/whitespace stripping is safe. Pass
  `minifier=` (e.g. esbuild) for app-authored code.
- **Shared runtime chunk**: one cached `runtime.<hash>.js` across all
  pages; measured at ~2.3KB total JS for counter+todo+blog
  (`python -m pyweb.bench`).

## Verify

`--budget static/home.<hash>.js=20KB` fails the build on breach —
wire it into CI before promoting the release.
