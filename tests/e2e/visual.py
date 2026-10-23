"""Screenshot baselines for the UI kit.

Shots use a bundled font (Inter, tests/e2e/fonts) with animations and the caret turned off, so a
render here matches one on CI (Playwright is pinned there, so it's the same Chromium). A shot
passes when at most 0.1% of its pixels differ by more than 24 (of 255) in some channel.
``PYWEB_UPDATE_SNAPSHOTS=1`` writes new baselines; a failure leaves the actual image and a diff in
test-results/.
"""

import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS = os.path.join(HERE, "snapshots")
RESULTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "test-results")
UPDATE = os.environ.get("PYWEB_UPDATE_SNAPSHOTS") == "1"
TOLERANCE = 0.001
THRESHOLD = 24


def _font_css():
    out = []
    for weight in (400, 500, 600, 700):
        with open(os.path.join(HERE, "fonts", f"inter-latin-{weight}-normal.woff2"), "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        out.append(f"@font-face{{font-family:InterTest;font-weight:{weight};font-style:normal;"
                   f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")
    return "".join(out)


STABLE_CSS = _font_css() + (
    "*,*::before,*::after{font-family:InterTest!important;animation:none!important;transition:none!important;"
    "caret-color:transparent!important;-webkit-font-smoothing:antialiased}")


def stabilize(page):
    """Bundled font, no animation. The page's context needs bypass_csp=True."""
    page.add_style_tag(content=STABLE_CSS)
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(50)


_DIFF_JS = """async ([a, b, threshold]) => {
  const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  const [x, y] = await Promise.all([load(a), load(b)]);
  if (x.width !== y.width || x.height !== y.height) return {size: [x.width, x.height, y.width, y.height]};
  const w = x.width, h = x.height;
  const pixels = (img) => { const c = new OffscreenCanvas(w, h); const g = c.getContext("2d"); g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, w, h).data; };
  const p = pixels(x), q = pixels(y);
  const out = new OffscreenCanvas(w, h), g = out.getContext("2d"), d = g.createImageData(w, h);
  let changed = 0;
  for (let i = 0; i < p.length; i += 4) {
    const off = Math.max(Math.abs(p[i] - q[i]), Math.abs(p[i + 1] - q[i + 1]), Math.abs(p[i + 2] - q[i + 2]),
                         Math.abs(p[i + 3] - q[i + 3]));
    if (off > threshold) { changed++; d.data[i] = 255; d.data[i + 3] = 255; }
    else { d.data[i] = d.data[i + 1] = d.data[i + 2] = p[i] / 3 + 170; d.data[i + 3] = 255; }
  }
  g.putImageData(d, 0, 0);
  const blob = await out.convertToBlob();
  const diff = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
  return {changed, total: w * h, diff};
}"""


def compare(browser, name, png):
    """Check a screenshot (PNG bytes) against snapshots/<name>.png."""
    path = os.path.join(SNAPSHOTS, name + ".png")
    if UPDATE:
        os.makedirs(SNAPSHOTS, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(png)
        return
    if not os.path.exists(path):
        raise AssertionError(f"no baseline for {name}: run with PYWEB_UPDATE_SNAPSHOTS=1 and commit {path}")
    with open(path, "rb") as fh:
        expected = fh.read()
    url = lambda data: "data:image/png;base64," + base64.b64encode(data).decode()  # noqa: E731
    ctx = browser.new_context()
    try:
        res = ctx.new_page().evaluate(_DIFF_JS, [url(expected), url(png), THRESHOLD])
    finally:
        ctx.close()
    problem = None
    if "size" in res:
        problem = f"size changed from {res['size'][0]}x{res['size'][1]} to {res['size'][2]}x{res['size'][3]}"
    elif res["changed"] / res["total"] > TOLERANCE:
        problem = f"{res['changed'] / res['total']:.2%} of pixels changed (allowed {TOLERANCE:.1%})"
    if problem:
        os.makedirs(RESULTS, exist_ok=True)
        with open(os.path.join(RESULTS, name + "-actual.png"), "wb") as fh:
            fh.write(png)
        if "diff" in res:
            with open(os.path.join(RESULTS, name + "-diff.png"), "wb") as fh:
                fh.write(base64.b64decode(res["diff"].split(",", 1)[1]))
        raise AssertionError(f"{name}: {problem}; see test-results/{name}-actual.png and -diff.png "
                             f"(PYWEB_UPDATE_SNAPSHOTS=1 accepts the change)")
