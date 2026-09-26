# 09 - Styling

> Source: `pyweb/css.py` (tested in `tests/test_browser_build.py`).
> Plain `.css` files keep working untouched — everything below is
> progressive enhancement, not a proprietary styling prison.

## Four levels

```python
from pyweb import css

cls, sheet = css.css({"padding": 12, "border_radius": 8})
# cls -> "c_a6c95f" (content hash), sheet -> ".c_a6c95f{padding:..."
```

Bare numbers become `px`; `opacity`, `z_index`, `flex`, `font_weight`
stay unitless. Underscores become hyphens.

```python
tokens = css.Tokens(primary="#635bff", space_md="16px")
tokens.var("primary")   # "var(--primary)"
tokens.stylesheet()     # ":root{--primary:#635bff;--space-md:16px}"
```

```python
mapping, scoped = css.module(".btn { color: red; }")
# mapping -> {"btn": "btn_m_5b68b0"}, element selectors pass through
```

```python
css.tw("flex gap-4")  # marked intentional; emitted verbatim for Tailwind
```

## Production

`pyweb build --production` extracts `<style>` blocks into hashed
per-page `.css` files linked from the SSR shell — no duplicated
inline styles, immutable caching per page.
