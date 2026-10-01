"""Build the PyWeb docs site into website/dist/ (stdlib only).

Usage:  python website/build.py
Output: website/dist/*.html + assets/* + search_index.json + sitemap.xml

Design goals: fast, accessible, comprehensive, honest. Every compiler
artifact shown on the site (compiled JS, SSR HTML, inspect output,
benchmark numbers) is produced at build time by importing the real
PyWeb compiler — no mockups, no hand-written "sample output".
"""

from __future__ import annotations

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "dist")
SITE = "https://maanavkrishna.github.io/PyWeb"

sys.path.insert(0, ROOT)

from pyweb.compiler import compile_source  # noqa: E402
from pyweb.bench import run as bench_run  # noqa: E402
from pyweb.build import minify_js  # noqa: E402

BENCH = bench_run()
with open(os.path.join(ROOT, "pyweb", "runtime", "browser", "runtime.js")) as fh:
    RUNTIME_JS = fh.read()
RUNTIME_MIN = len(minify_js(RUNTIME_JS).encode())


def compile_example(app):
    path = os.path.join(ROOT, "examples", app, "app.pyweb")
    with open(path) as fh:
        src = fh.read()
    out = compile_source(src, filename="app.pyweb")
    page = next(iter(out["pages"].values()))
    return src, out, page


EXAMPLES = {}
for _app in ("counter", "todo", "blog", "auth", "chat", "showcase"):
    try:
        EXAMPLES[_app] = compile_example(_app)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: example {_app} failed to compile: {exc}")

COUNTER_SRC, COUNTER_OUT, COUNTER_PAGE = EXAMPLES["counter"]
TODO_SRC, TODO_OUT, TODO_PAGE = EXAMPLES["todo"]
# --------------------------------------------------------------------------
# Design system (single stylesheet, dark/light, accessible, responsive)
# --------------------------------------------------------------------------

CSS = """\
:root{
--bg:#0a0f16;--bg2:#0d1420;--panel:#111a29;--panel2:#182437;--line:#233350;
--txt:#e9eef7;--dim:#9dabc3;--faint:#67778f;
--acc:#34d399;--acc-ink:#05281c;--acc2:#7dd3fc;--ok:#34d399;
--warn:#fbbf24;--err:#fb7185;--code:#0c1220;
--grad:linear-gradient(135deg,#34d399 0%,#2dd4bf 50%,#7dd3fc 100%);
--radius:14px;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Inter,"Helvetica Neue",sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
[data-theme=light]{
--bg:#f6f8fb;--bg2:#ffffff;--panel:#ffffff;--panel2:#eaf0f7;--line:#d7e1ee;
--txt:#0f1c2e;--dim:#4c6076;--faint:#7d90a8;
--acc:#047857;--acc-ink:#ffffff;--acc2:#0369a1;--ok:#047857;
--warn:#92400e;--err:#be123c;--code:#eef3f9;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font:16px/1.7 var(--sans);background:var(--bg);color:var(--txt);-webkit-font-smoothing:antialiased}
::selection{background:var(--acc);color:var(--acc-ink)}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--acc2);outline-offset:2px;border-radius:6px}
.skip{position:absolute;left:-999px;top:0;background:var(--acc);color:var(--acc-ink);padding:8px 14px;z-index:99;border-radius:0 0 8px 0;font-weight:700}
.skip:focus{left:0}
/* Hero ambient glows (positioned with Tailwind; themed opacity here) */
.glow{filter:blur(100px);opacity:.22;pointer-events:none;border-radius:9999px}
[data-theme=light] .glow{opacity:.13}
/* ---------- content ---------- */
section.block{margin:54px 0;counter-increment:pwsec}
h2.sec{font-size:clamp(23px,3.2vw,30px);margin:0 0 8px;letter-spacing:-.6px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
section.block h2.sec::before{content:counter(pwsec,decimal-leading-zero);font-size:12px;font-weight:800;font-family:var(--mono);color:var(--acc-ink);background:var(--acc);border-radius:7px;padding:2px 8px;letter-spacing:0}
.hero h2.sec::before,.doc>section.block:first-child h2.sec::before{content:none}
p.lead{color:var(--dim);max-width:800px}
p.stamp{font-size:13px;color:var(--faint)}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:99px;background:var(--ok);margin-right:6px;box-shadow:0 0 8px var(--ok)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:26px 0}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:26px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:22px;position:relative;transition:border-color .15s,transform .15s}
a.card:hover{border-color:var(--acc);text-decoration:none;transform:translateY(-2px)}
.card h3{margin:0 0 8px;font-size:16.5px}
.card p{margin:0;color:var(--dim);font-size:14.5px}
pre{background:var(--code);border:1px solid var(--line);border-radius:12px;padding:18px;overflow:auto;font-size:13.8px;line-height:1.6;position:relative}
code{font-family:var(--mono)}
p code,li code,td code{font-size:.85em;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:1px 7px}
pre code{background:none;border:none;padding:0}
.codehead{display:flex;gap:8px;align-items:center;margin:18px 0 -6px;font-size:12.5px;color:var(--faint)}
.codehead .fn{font-family:var(--mono);color:var(--dim)}
.codehead .bytes{margin-left:auto;font-family:var(--mono)}
.copybtn{position:absolute;top:10px;right:10px;background:var(--panel2);border:1px solid var(--line);color:var(--dim);border-radius:8px;font-size:12px;padding:4px 10px;cursor:pointer;font-family:var(--sans)}
.copybtn:hover{color:var(--txt);border-color:var(--acc)}
.tabset{border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:20px 0;background:var(--code)}
.tabset::before{content:"● ● ●";letter-spacing:4px;font-size:10px;color:var(--faint);display:block;padding:12px 18px 0}
.tabs{display:flex;gap:6px;flex-wrap:wrap;padding:8px 14px 0}
.tabbtn{background:transparent;border:1px solid transparent;border-bottom:none;color:var(--dim);border-radius:9px 9px 0 0;padding:9px 18px;font-size:13.5px;font-weight:600;cursor:pointer;font-family:var(--sans)}
.tabbtn:hover{color:var(--txt)}
.tabbtn[aria-selected=true]{color:var(--txt);background:var(--panel2);border-color:var(--line)}
.tabbody{display:none;padding:0 14px 14px}
.tabbody.on{display:block}
.tabbody pre{border:none;margin:0;background:transparent;padding:14px 4px}
table.spec{width:100%;border-collapse:collapse;font-size:14.5px;margin:16px 0}
table.spec th,table.spec td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
table.spec th{color:var(--faint);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.6px}
table.spec tr:hover td{background:var(--panel)}
.warn,.ok,.info{border-radius:12px;padding:14px 18px;font-size:14.5px;margin:18px 0;border:1px solid}
.warn{background:rgba(251,191,36,.08);border-color:rgba(251,191,36,.35)}
.ok{background:rgba(52,211,153,.07);border-color:rgba(52,211,153,.32)}
.info{background:rgba(125,211,252,.09);border-color:rgba(125,211,252,.38)}
[data-theme=light] .warn{background:#fff7e6;border-color:#d9a441}
[data-theme=light] .ok{background:#e6f6ec;border-color:#7cc79c}
[data-theme=light] .info{background:#e8f4fd;border-color:#a5cdf0}
.k{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:1px 8px;font-size:12.5px;font-family:var(--mono)}
.pill{display:inline-block;border-radius:99px;padding:1px 11px;font-size:12px;font-weight:700}
.pill.real{background:rgba(52,211,153,.12);color:var(--ok);border:1px solid rgba(52,211,153,.4)}
.pill.preview{background:rgba(251,191,36,.12);color:var(--warn);border:1px solid rgba(251,191,36,.4)}
.pill.future{background:var(--panel2);color:var(--faint);border:1px solid var(--line)}
.bench{margin:14px 0}
.brow{display:grid;grid-template-columns:110px 1fr 130px;gap:12px;align-items:center;margin:9px 0;font-size:13.5px}
.brow .bl{font-family:var(--mono);color:var(--dim)}
.btrack{background:var(--panel2);border-radius:7px;height:16px;overflow:hidden}
.bfill{height:100%;border-radius:7px;background:linear-gradient(90deg,#34d399,#7dd3fc)}
.bfill.tiny{background:linear-gradient(90deg,#2dd4bf,#34d399)}
.brow .bv{font-family:var(--mono);text-align:right;color:var(--dim)}
.graph{background:var(--code);border:1px solid var(--line);border-radius:12px;padding:22px;overflow:auto;margin:16px 0}
.graph svg{max-width:100%;height:auto}
.gnode{fill:var(--panel2);stroke:var(--acc2);stroke-width:1.2}
.gtxt{fill:var(--txt);font:12px var(--mono)}
.glab{fill:var(--faint);font:11px var(--sans)}
.gedge{stroke:var(--acc);stroke-width:1.6}
.pg-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;counter-reset:pg}
@media(max-width:860px){.pg-grid{grid-template-columns:1fr}}
.pg-step{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:20px;position:relative;counter-increment:pg}
.pg-step::after{content:counter(pg,decimal-leading-zero);position:absolute;top:14px;right:16px;font-family:var(--mono);font-size:12px;font-weight:800;color:var(--faint)}
.pg-step h4{margin:0 0 4px;font-size:13px;color:var(--faint);text-transform:uppercase;letter-spacing:.5px}
ol.steps{padding-left:22px}ol.steps li{margin:10px 0}
ol.steps li::marker{color:var(--acc);font-weight:800}
ul.check{list-style:none;padding:0}ul.check li{margin:7px 0;padding-left:28px;position:relative}
ul.check li::before{content:"✓";position:absolute;left:2px;color:var(--ok);font-weight:800}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.compare{grid-template-columns:1fr}}
.vs-no,.vs-yes{border-radius:12px;padding:16px 18px;border:1px solid}
.vs-no{background:rgba(255,143,163,.07);border-color:rgba(255,143,163,.32)}
.vs-yes{background:rgba(110,231,160,.07);border-color:rgba(110,231,160,.32)}
[data-theme=light] .vs-no{background:#fdeef0;border-color:#e8a0ac}
[data-theme=light] .vs-yes{background:#e6f6ec;border-color:#7cc79c}
.pager{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:54px 0 10px}
@media(max-width:640px){.pager{grid-template-columns:1fr}}
.pager a{border:1px solid var(--line);border-radius:12px;padding:14px 18px;background:var(--panel);color:var(--txt)}
.pager a:hover{border-color:var(--acc);text-decoration:none}
.pager small{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--faint)}
.pager .next{text-align:right}
.pager .only{grid-column:1/-1}
@media print{header,aside,footer,.tabs,.copybtn,.pager{display:none}}
"""

JS = """\
function pwTabs(root){root=root||document;root.querySelectorAll(".tabset").forEach(function(set){var btns=set.querySelectorAll(".tabbtn");var bodies=set.querySelectorAll(".tabbody");btns.forEach(function(b,i){b.addEventListener("click",function(){btns.forEach(function(x){x.setAttribute("aria-selected","false")});bodies.forEach(function(x){x.classList.remove("on")});b.setAttribute("aria-selected","true");bodies[i].classList.add("on")})})})}
function pwCopy(btn){var pre=btn.parentElement.querySelector("code");var t=pre?pre.innerText:"";if(navigator.clipboard){navigator.clipboard.writeText(t).then(function(){btn.textContent="Copied \\u2713";setTimeout(function(){btn.textContent="Copy"},1400)})}}
function pwTheme(){var d=document.documentElement;var cur=d.getAttribute("data-theme");var next=cur==="light"?"":"light";if(next){d.setAttribute("data-theme",next)}else{d.removeAttribute("data-theme")}try{localStorage.setItem("pw-theme",next)}catch(e){}}
(function(){try{var t=localStorage.getItem("pw-theme");if(!t&&window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches){t="light"}if(t){document.documentElement.setAttribute("data-theme",t)}}catch(e){}document.addEventListener("DOMContentLoaded",function(){pwTabs();var sb=document.getElementById("site-search");if(sb){sb.addEventListener("keydown",function(e){if(e.key==="Enter"&&sb.value.trim()){location.href="search.html?q="+encodeURIComponent(sb.value.trim())}})}})})();
"""
# --------------------------------------------------------------------------
# Page chrome + helpers
# --------------------------------------------------------------------------

NAV_GROUPS = [
    ("Start", [("Guide", "guide.html"), ("Playground", "playground.html")]),
    (
        "Core",
        [
            ("Reactivity", "reactivity.html"),
            ("RPC & Placement", "rpc.html"),
            ("Database", "database.html"),
            ("Auth", "auth.html"),
        ],
    ),
    (
        "Runtime",
        [
            ("Realtime & Jobs", "realtime.html"),
            ("Styling", "styling.html"),
            ("Browser APIs", "browser.html"),
            ("Testing", "testing.html"),
        ],
    ),
    (
        "Ship",
        [
            ("Production", "production.html"),
            ("Deploy", "deploy.html"),
            ("API", "api.html"),
            ("Examples", "examples.html"),
            ("Benchmarks", "benchmarks.html"),
            ("Roadmap", "roadmap.html"),
        ],
    ),
]

NAV = [(label, href) for _, items in NAV_GROUPS for label, href in items]
NAV_INDEX = {href: i for i, (_, href) in enumerate(NAV)}

SEARCH_TEXTS = {}


TOP_LINKS = [
    ("Guide", "guide.html"),
    ("Playground", "playground.html"),
    ("Examples", "examples.html"),
    ("Benchmarks", "benchmarks.html"),
    ("API", "api.html"),
]

_GH_SVG = (
    "<svg width=14 height=14 viewBox='0 0 16 16' fill='currentColor' aria-hidden=true>"
    "<path d='M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 "
    "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 "
    "1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
    "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 "
    "2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 "
    "3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z'/></svg>"
)

_LINK_BASE = (
    "whitespace-nowrap rounded-lg px-2.5 py-1.5 hover:bg-panel2 "
    "hover:text-ink hover:no-underline"
)


def nav_html(active=""):
    items = "".join(
        '<a href="%s" class="%s %s">%s</a>'
        % (
            href,
            "bg-panel2 font-bold text-ink" if href == active else "text-mut",
            _LINK_BASE,
            label,
        )
        for label, href in TOP_LINKS
    )
    return (
        '<a class="skip" href="#main">Skip to content</a>'
        '<header class="sticky top-0 z-50 border-b border-line bg-base2">'
        '<div class="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-x-3 gap-y-2 px-6 py-2.5">'
        '<a class="flex items-center gap-2 text-[18px] font-extrabold tracking-tight '
        'text-ink hover:no-underline" href="index.html">'
        '<span class="grid h-[26px] w-[26px] place-items-center rounded-lg '
        'bg-gradient-to-br from-emerald-400 to-sky-400 text-[14px] font-black text-white">P</span>'
        "PyWeb</a>"
        '<span class="whitespace-nowrap rounded-full border border-line bg-panel2 '
        'px-2.5 py-0.5 text-[11px] font-bold text-okc">v1.0</span>'
        '<nav class="ml-auto hidden items-center gap-0.5 text-[13px] md:flex" '
        'aria-label="Docs">%s</nav>'
        '<div class="ml-auto flex items-center gap-2 md:ml-0">'
        '<input id="site-search" type="search" placeholder="Search docs…" aria-label="Search docs" '
        'class="hidden w-[150px] rounded-lg border border-line bg-panel px-3 py-1.5 '
        'text-[13px] text-ink placeholder:text-faint sm:block xl:w-[170px]">'
        '<button onclick="pwTheme()" title="Toggle theme" '
        'class="rounded-lg border border-line bg-panel px-3 py-1.5 text-[13px] '
        'text-ink hover:border-acc">☾</button>'
        '<a href="https://github.com/MaanavKrishna/PyWeb" aria-label="GitHub" '
        'class="hidden items-center gap-1.5 rounded-lg border border-line bg-panel '
        'px-3 py-1.5 text-[13px] font-semibold text-ink hover:border-acc '
        'hover:no-underline sm:inline-flex">%s<span>GitHub</span></a>'
        "</div></div></header>" % (items, _GH_SVG)
    )


def side_html(active=""):
    groups = "".join(
        '<div class="shrink-0 lg:mb-5">'
        '<div class="mb-1.5 hidden text-[11px] font-extrabold uppercase '
        'tracking-[1px] text-faint lg:block">%s</div>'
        '<nav aria-label="%s" class="flex gap-2 overflow-x-auto pb-1 lg:block '
        'lg:overflow-visible lg:pb-0">%s</nav></div>'
        % (
            html.escape(gname),
            html.escape(gname),
            "".join(
                '<a href="%s" class="%s">%s</a>'
                % (
                    href,
                    "mb-0 block whitespace-nowrap rounded-lg border px-3 py-1.5 "
                    "hover:bg-panel hover:text-ink hover:no-underline "
                    "border-line text-mut lg:mb-1 lg:border-0 lg:border-l-2 "
                    "lg:border-transparent"
                    if href != active
                    else "mb-0 block whitespace-nowrap rounded-lg border px-3 py-1.5 "
                    "hover:bg-panel hover:text-ink hover:no-underline "
                    "border-acc text-ink lg:mb-1 lg:border-0 lg:border-l-2 "
                    "lg:bg-panel lg:font-bold",
                    html.escape(label),
                )
                for label, href in items
            ),
        )
        for gname, items in NAV_GROUPS
    )
    return (
        '<aside class="flex gap-6 overflow-x-auto py-3 lg:sticky lg:top-[62px] '
        'lg:block lg:max-h-[calc(100vh_-_86px)] lg:overflow-auto lg:py-7">%s</aside>'
        % groups
    )


def pager_html(active=""):
    if active not in NAV_INDEX:
        return ""
    i = NAV_INDEX[active]
    prev_html = next_html = ""
    if i > 0:
        pl, ph = NAV[i - 1]
        prev_html = (
            '<a href="%s"><small>← Previous</small><b>%s</b></a>' % (ph, html.escape(pl))
        )
    if i < len(NAV) - 1:
        nl, nh = NAV[i + 1]
        next_html = (
            '<a class="next" href="%s"><small>Next →</small><b>%s</b></a>'
            % (nh, html.escape(nl))
        )
    if not prev_html and not next_html:
        return ""
    if not prev_html:
        return '<nav class="pager" aria-label="Page">%s</nav>' % next_html.replace(
            ' class="next"', ' class="next only"'
        )
    return '<nav class="pager" aria-label="Pages">%s%s</nav>' % (prev_html, next_html)


TAILWIND_HEAD = (
    '<script src="https://cdn.tailwindcss.com"></script>'
    "<script>tailwind.config={corePlugins:{preflight:!1},theme:{extend:{"
    'colors:{base:"var(--bg)",base2:"var(--bg2)",panel:"var(--panel)",'
    'panel2:"var(--panel2)",line:"var(--line)",ink:"var(--txt)",'
    'mut:"var(--dim)",faint:"var(--faint)",acc:"var(--acc)",'
    'acc2:"var(--acc2)",okc:"var(--ok)"},'
    'fontFamily:{sans:["system-ui","-apple-system","Segoe UI","Roboto",'
    '"Inter","sans-serif"],mono:["ui-monospace","SFMono-Regular","Menlo",'
    '"Consolas","monospace"]}}}}</script>'
)

_FOOT_COLS = (
    ("Learn", [("5-minute guide", "guide.html"), ("Compiler playground", "playground.html"),
               ("Reactivity", "reactivity.html"), ("RPC & placement", "rpc.html")]),
    ("Build", [("Database", "database.html"), ("Auth", "auth.html"),
               ("Realtime & jobs", "realtime.html"), ("Deploy", "deploy.html")]),
    ("Project", [("GitHub", "https://github.com/MaanavKrishna/PyWeb"),
                 ("Benchmarks", "benchmarks.html"), ("API map", "api.html"),
                 ("Roadmap", "roadmap.html")]),
)


def foot_html():
    cols = "".join(
        '<div><h5 class="mb-2.5 text-[11.5px] font-bold uppercase '
        'tracking-[0.8px] text-faint">%s</h5>'
        '<ul class="m-0 list-none p-0">%s</ul></div>'
        % (
            h,
            "".join(
                '<li class="my-1.5"><a class="text-mut hover:text-ink" '
                'href="%s">%s</a></li>' % (href, label)
                for label, href in links
            ),
        )
        for h, links in _FOOT_COLS
    )
    return (
        '<footer class="mt-[70px] border-t border-line bg-base2 py-11 '
        'text-[13.5px] text-mut">'
        '<div class="mx-auto w-full max-w-7xl px-6">'
        '<div class="grid gap-7 md:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">'
        '<div><h5 class="mb-2.5 text-[11.5px] font-bold uppercase '
        'tracking-[0.8px] text-faint">PyWeb</h5>'
        '<p class="m-0 max-w-[340px]">One language. Every layer. '
        "Python from browser to database — compiler, reactivity, RPC, auth, realtime, and "
        "deploy-anywhere builds. Core open source (MIT).</p></div>"
        "%s</div>"
        '<div class="mt-7 flex flex-wrap items-center gap-4 border-t '
        'border-line pt-5 text-[12.5px] text-faint">'
        "<span>PyWeb — Python from browser to database. "
        "Self-host anywhere; no mandatory cloud.</span>"
        '<span class="ml-auto flex gap-4">'
        '<a href="https://github.com/MaanavKrishna/PyWeb">GitHub</a>'
        '<a href="roadmap.html">Roadmap</a>'
        '<a href="benchmarks.html">Benchmarks</a></span></div>'
        "</div></footer>" % cols
    )


def page(name, title, desc, body, active="", wide=False):
    text = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()[:6000]
    SEARCH_TEXTS[name] = {"title": title, "text": text}
    if wide:
        content = (
            '<main id="main" class="mx-auto w-full max-w-6xl px-6">%s</main>'
            % body
        )
    else:
        content = (
            '<div id="main" class="mx-auto w-full max-w-7xl gap-9 px-6 '
            'lg:grid lg:grid-cols-[250px_minmax(0,1fr)]">%s'
            '<div class="min-w-0 pb-5">%s%s</div></div>'
            % (side_html(active), body, pager_html(active))
        )
    shell = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta name=description content='%s'>"
        "<meta property='og:title' content='%s - PyWeb'>"
        "<meta property='og:description' content='%s'>"
        "<meta property='og:type' content='website'>"
        "<meta name=theme-color content='#0a0f16'>"
        "<title>%s - PyWeb</title>" + TAILWIND_HEAD +
        '<link rel=stylesheet href="assets/style.css">'
        '<link rel=icon href="data:image/svg+xml,<svg xmlns=%s viewBox=%s><text y=%s font-size=%s>P</text></svg>">'
        "</head>" + '<body class="m-0 bg-base font-sans text-ink">%s%s%s'
        '<script src="assets/site.js"></script></body></html>'
    )
    return shell % (
            html.escape(desc, quote=True),
            html.escape(title),
            html.escape(desc, quote=True),
            html.escape(title),
            "%27http://www.w3.org/2000/svg%27",
            "%270 0 32 32%27",
            "%27.9em%27",
            "%2732%27",
            nav_html(active),
            content,
            foot_html(),
        )


def code(s, lang=""):
    return '<div style="position:relative"><button class="copybtn" onclick="pwCopy(this)">Copy</button><pre><code>%s</code></pre></div>' % html.escape(
        s
    )


def codehead(filename, nbytes, note=""):
    extra = " · " + html.escape(note) if note else ""
    return '<div class="codehead"><span class="fn">%s</span><span class="bytes">%s bytes%s</span></div>' % (
        html.escape(filename),
        f"{nbytes:,}",
        extra,
    )


def tabs(named_blocks):
    btns = "".join(
        '<button class="tabbtn" role="tab" aria-selected="%s">%s</button>'
        % ("true" if i == 0 else "false", html.escape(label))
        for i, (label, _) in enumerate(named_blocks)
    )
    bodies = "".join(
        '<div class="tabbody%s">%s</div>' % (" on" if i == 0 else "", body)
        for i, (_, body) in enumerate(named_blocks)
    )
    return '<div class="tabset"><div class="tabs" role="tablist">%s</div>%s</div>' % (
        btns,
        bodies,
    )


def bench_bars(rows, unit="B", scale=None):
    scale = scale or max(v for _, v, _ in rows)
    out = ['<div class="bench">']
    for label, value, cls in rows:
        pct = max(2, round(100 * value / scale))
        out.append(
            '<div class="brow"><span class="bl">%s</span>'
            '<span class="btrack"><span class="bfill %s" style="display:block;width:%d%%"></span></span>'
            '<span class="bv">%s %s</span></div>'
            % (html.escape(label), cls, pct, f"{value:,}", unit)
        )
    out.append("</div>")
    return "".join(out)


PAGES = {}


def add(name, title, desc, body, active="", wide=False):
    PAGES[name] = (title, desc, body, active or name, wide)
# --------------------------------------------------------------------------
# Home page — hero, live-compiled demo, stats, feature map
# --------------------------------------------------------------------------

_counter_js = COUNTER_PAGE["js"]
_counter_html = COUNTER_PAGE["html_body"]
_counter_sigs = ", ".join(COUNTER_PAGE["signals"])
_counter_place = "<br>".join(
    "<span class=k>%s</span> → <b>%s</b> <span style='color:var(--faint)'>%s</span>"
    % (html.escape(sym), html.escape(loc), html.escape(reason))
    for sym, (loc, reason) in COUNTER_PAGE["placement"].items()
)
_bench_rows = [
    (name, BENCH[name]["js_bytes"], "tiny" if name == "counter" else "")
    for name in ("counter", "todo", "blog")
]
_total_js = sum(BENCH[n]["js_bytes"] for n in BENCH)

add(
    "index.html",
    "Python from browser to database",
    "PyWeb is a compiler and runtime for building complete applications in "
    "Python: fine-grained reactivity, automatic RPC, real databases, auth, "
    "realtime, offline, and deploy-anywhere builds.",
    """
<section class="relative overflow-hidden px-6 py-24 text-center sm:py-28">
<div aria-hidden="true" class="glow absolute -left-36 -top-40 h-[480px] w-[480px] bg-emerald-400"></div>
<div aria-hidden="true" class="glow absolute -right-40 -top-32 h-[520px] w-[520px] bg-sky-400"></div>
<div class="relative">
<div class="mb-5 inline-flex items-center gap-2 rounded-full border border-line bg-panel px-4 py-1 text-[12.5px] font-bold text-mut"><span class="h-2 w-2 rounded-full bg-gradient-to-br from-emerald-400 to-sky-400"></span>v1.0 &middot; production-grade &middot; MIT open source</div>
<h1 class="mx-auto max-w-3xl text-5xl font-extrabold leading-[1.02] tracking-tight sm:text-6xl lg:text-7xl">One language. <span class="bg-gradient-to-r from-emerald-400 via-teal-300 to-sky-400 bg-clip-text text-transparent">Every layer.</span></h1>
<p class="mx-auto mt-5 max-w-2xl text-lg leading-relaxed text-mut sm:text-xl">Write one coherent Python app. PyWeb infers what runs in the
browser vs the server, turns plain variables into fine-grained reactive
state, and generates typed RPC &mdash; no manual APIs, no state library, no
bundler config.</p>
<div class="mt-8 flex flex-wrap justify-center gap-3">
<a class="rounded-xl bg-gradient-to-r from-emerald-500 to-sky-500 px-7 py-3.5 text-[15.5px] font-bold text-white shadow-lg shadow-emerald-500/20 transition hover:-translate-y-px hover:brightness-110 hover:no-underline" href="guide.html">Get started in 5 minutes &rarr;</a>
<a class="rounded-xl border border-line bg-panel px-7 py-3.5 text-[15.5px] font-bold text-ink hover:border-acc hover:no-underline" href="playground.html">See the compiler work</a>
<a class="rounded-xl border border-line bg-panel px-7 py-3.5 text-[15.5px] font-bold text-ink hover:border-acc hover:no-underline" href="examples.html">Browse examples</a>
</div>
<div class="mx-auto mt-12 grid max-w-4xl grid-cols-2 gap-px overflow-hidden rounded-2xl border border-line bg-line lg:grid-cols-4">
<div class="bg-panel px-3 py-5 text-center"><div class="text-[23px] font-extrabold tracking-tight">~{rt} KB</div><div class="mt-1 text-[12px] text-faint">shared browser runtime (minified)</div></div>
<div class="bg-panel px-3 py-5 text-center"><div class="text-[23px] font-extrabold tracking-tight">&lt;1 KB</div><div class="mt-1 text-[12px] text-faint">typical per-page JS (counter: {cj} B)</div></div>
<div class="bg-panel px-3 py-5 text-center"><div class="text-[23px] font-extrabold tracking-tight">{ssr} ms</div><div class="mt-1 text-[12px] text-faint">SSR compile (counter)</div></div>
<div class="bg-panel px-3 py-5 text-center"><div class="text-[23px] font-extrabold tracking-tight">0</div><div class="mt-1 text-[12px] text-faint">hand-written API routes for RPC</div></div>
</div></div></section>

<section class=block>
<h2 class=sec>Plain variables are reactive</h2>
<p class=lead>No <code>Signal(0)</code>, no hooks. The compiler sees
<code>count</code> read by markup and mutated by an event, lowers it to a
signal, and patches only the affected text node — no virtual DOM.
Everything below is the <b>actual compiler output</b> for this source,
generated when this site was built:</p>
""".format(rt=round(RUNTIME_MIN / 1024, 1), cj=len(_counter_js.encode()),
           ssr=BENCH["counter"]["ssr_ms"])
    + tabs(
        [
            ("app.pyweb", code(COUNTER_SRC)),
            (
                "compiled page JS · %d B" % len(_counter_js.encode()),
                code(_counter_js),
            ),
            ("SSR HTML", code(_counter_html)),
        ]
    )
    + """
<p class=lead style="margin-top:14px">Reactivity analysis:
<span class=k>signals: {sigs}</span></p>
<p class=lead>Placement analysis:<br>{place}</p>
</section>

<section class=block>
<h2 class=sec>How a request flows</h2>
<p class=lead>One mental model, five stages. Click a stage to read its guide:</p>
<div class=grid4>
<a class=card href="reactivity.html"><span class="absolute right-4 top-3.5 font-mono text-[11.5px] font-extrabold text-faint">01</span><div class="text-[22px]">⚡</div><h3>React</h3><p>Signals, computed values, effects — inferred from plain Python.</p></a>
<a class=card href="rpc.html"><span class="absolute right-4 top-3.5 font-mono text-[11.5px] font-extrabold text-faint">02</span><div class="text-[22px]">🔌</div><h3>Place + call</h3><p>Browser/server partitioning with reasons; typed RPC stubs.</p></a>
<a class=card href="database.html"><span class="absolute right-4 top-3.5 font-mono text-[11.5px] font-extrabold text-faint">03</span><div class="text-[22px]">🗄</div><h3>Persist</h3><p>Postgres-first models, pooling, journaled migrations.</p></a>
<a class=card href="production.html"><span class="absolute right-4 top-3.5 font-mono text-[11.5px] font-extrabold text-faint">04</span><div class="text-[22px]">📈</div><h3>Operate</h3><p>Traces, error codes, budgets, health checks, deploys.</p></a>
</div></section>

<section class=block>
<h2 class=sec>Shipped JavaScript, measured honestly</h2>
<p class=lead>Totals <b>include the shared runtime</b> (excluding it would be
benchmark theater). The runtime is cached across pages; per-page code is
typically under 1 KB. Static pages ship near-zero JS.</p>
""".format(sigs=html.escape(_counter_sigs), place=_counter_place)
    + bench_bars(_bench_rows)
    + """
<p class=stamp><span class=live-dot></span>Live numbers from
<code>python -m pyweb.bench</code> at site build time · total {total:,} B
across {n} apps · <a href="benchmarks.html">full benchmarks</a></p>
</section>

<section class=block>
<h2 class=sec>Everything a production app needs</h2>
<div class=grid3>
<div class=card><h3>🔒 Secure by default</h3><p>Parameterized SQL only, escaped templates, signed sessions, secret-leak compile errors, upload validation. <code>pyweb check</code> gates CI.</p></div>
<div class=card><h3>🔍 Every magic is inspectable</h3><p><code>pyweb inspect</code> prints placement decisions and reasons per symbol — browser vs server, and why.</p></div>
<div class=card><h3>🔄 Realtime + offline</h3><p>Live queries push minimal patches over SSE/WebSocket; offline queue replays with LWW conflicts and optimistic UI.</p></div>
<div class=card><h3>🗄 Real databases</h3><p>Postgres/MySQL/SQLite, pooling, prepared statements, streaming, retries, versioned migrations with journal and rollback.</p></div>
<div class=card><h3>🧪 Tests without a browser</h3><p>Virtual-browser client for full-stack tests; real-browser hooks when you need them.</p></div>
<div class=card><h3>📦 Deploy anywhere</h3><p><code>pyweb build --production</code> emits hashed assets + manifest; <code>pyweb serve</code> runs threaded with <code>/healthz</code>. No lock-in.</p></div>
</div></section>

<section class=block>
<div class=warn><b>Honest scope (v1.0):</b> streaming SSR, the WASM browser
target, and native mobile renderers are roadmap — see the
<a href="roadmap.html">roadmap page</a> for what is real, preview, and
future. No vaporware.</div>
</section>
""".format(total=_total_js, n=len(BENCH)),
    wide=True,
)
# --------------------------------------------------------------------------
# Guide, reactivity, RPC/placement, playground
# --------------------------------------------------------------------------

add(
    "guide.html",
    "Get started in 5 minutes",
    "Install PyWeb, scaffold an app, run the hot-reload dev server, check, "
    "build, and serve production.",
    """
<section class=block>
<h2 class=sec>Get started in 5 minutes</h2>
<p class=lead>Install, scaffold, run. The dev server compiles, serves, and
hot-reloads on save.</p>
"""
    + code(
        """pip install pyweb

pyweb new shop && cd shop
pyweb dev app.pyweb        # -> http://localhost:8000 (hot reload)

pyweb check app.pyweb      # types + secret-leak gate
pyweb build app.pyweb --out dist --production
pyweb serve dist           # threaded prod server, /healthz"""
    )
    + """
<ol class=steps>
<li><b>Scaffold</b> — <code>pyweb new shop</code> writes a counter
<code>app.pyweb</code>. Open it: one page, one variable, one event.</li>
<li><b>Run</b> — <code>pyweb dev</code> starts the threaded dev server with a
file watcher. Save → recompile → state-preserving reload where possible.</li>
<li><b>Grow</b> — add a <code>@server</code> function and call it like any
function; add a <code>Model</code>; add a second page. No API files appear.</li>
<li><b>Gate</b> — <code>pyweb check</code> fails CI on secret leaks and
vulnerabilities; <code>pyweb inspect</code> explains every placement.</li>
<li><b>Ship</b> — <code>pyweb build --production</code> emits hashed assets +
manifest; <code>pyweb serve</code> or any container platform runs it.</li>
</ol>
<h2 class=sec>CLI reference</h2>
<table class=spec>
<tr><th>Command</th><th>Does</th></tr>
<tr><td><span class=k>pyweb new NAME</span></td><td>Scaffold app.pyweb counter</td></tr>
<tr><td><span class=k>pyweb dev FILE [--port] [--no-reload]</span></td><td>Threaded dev server + hot-reload watcher</td></tr>
<tr><td><span class=k>pyweb serve DIR [--app mod:attr]</span></td><td>Serve production dist (never execs source)</td></tr>
<tr><td><span class=k>pyweb build FILE --production</span></td><td>Hashed assets, minified JS, split bundles, importmap, manifest, budgets</td></tr>
<tr><td><span class=k>pyweb check FILE</span></td><td>Compile + security findings; fails CI on secret-leak</td></tr>
<tr><td><span class=k>pyweb inspect FILE [--security]</span></td><td>Placement decisions with reasons, RPC list</td></tr>
<tr><td><span class=k>pyweb test [PATH]</span></td><td>Run pytest suite</td></tr>
<tr><td><span class=k>pyweb fmt / lint</span></td><td>Ruff when installed, stdlib fallback otherwise</td></tr>
<tr><td><span class=k>pyweb db migrate|status|new|rollback</span></td><td>Versioned migrations with journal + rollback</td></tr>
<tr><td><span class=k>pyweb npm FILE.d.ts</span></td><td>Typed Python stubs from TypeScript declarations</td></tr>
<tr><td><span class=k>pyweb deploy --target docker|compose|k8s</span></td><td>Dockerfile (healthcheck), compose, k8s (probes)</td></tr>
</table>
<p class=lead>Next: <a href="reactivity.html">reactivity without a VDOM</a> →
<a href="rpc.html">RPC &amp; placement</a> → <a href="examples.html">examples</a>.</p>
</section>
""",
)

_graph_svg = """
<div class="graph" role="img" aria-label="Dependency graph: price and quantity flow into total, which updates one text node">
<svg viewBox="0 0 640 210" width="640" height="210">
<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#6b7d99" stroke-width="1.4"/></marker></defs>
<text class="glab" x="20" y="18">pyweb compiler · reactive dependency graph</text>
<rect class="gnode" x="20" y="50" width="130" height="52" rx="10"/><text class="gtxt" x="34" y="72">price = 100</text><text class="glab" x="34" y="90">signal · browser</text>
<rect class="gnode" x="20" y="130" width="130" height="52" rx="10"/><text class="gtxt" x="34" y="152">quantity = 2</text><text class="glab" x="34" y="170">signal · browser</text>
<rect class="gnode" x="255" y="90" width="150" height="52" rx="10"/><text class="gtxt" x="269" y="112">total = price*qty</text><text class="glab" x="269" y="130">computed</text>
<rect class="gnode" x="490" y="90" width="130" height="52" rx="10"/><text class="gtxt" x="504" y="112">&lt;p&gt;{total}&lt;/p&gt;</text><text class="glab" x="504" y="130">1 text node</text>
<line class="gedge" x1="150" y1="76" x2="248" y2="104"/><line class="gedge" x1="150" y1="156" x2="248" y2="122"/><line class="gedge" x1="405" y1="116" x2="483" y2="116"/>
</svg></div>
"""

add(
    "reactivity.html",
    "Reactivity without a virtual DOM",
    "How PyWeb lowers ordinary Python variables into signals and computed "
    "values, and patches only the affected DOM nodes.",
    """
<section class=block>
<h2 class=sec>Normal variables become signals</h2>
<p class=lead>Stages: parse markup → <code>compute_reactive</code> finds
locals read by UI and mutated by handlers → each becomes a signal →
computed values form a dependency graph → generated JS updates only bound
nodes.</p>
"""
    + code(
        """price = 100
quantity = 2
total = price * quantity   # compiler: computed(price, quantity)

<p>{total}</p>             # one text-node binding; a price change
                           # patches that node only"""
    )
    + _graph_svg
    + """
<p class=lead>When <code>price</code> changes, only the <code>{total}</code>
text node updates — the component never rerenders. Compare with the
framework norm:</p>
<div class=compare>
<div class=vs-no><b>Typical VDOM framework</b><br>state change → rerender component → diff tree → patch DOM. O(tree) work per update; memoization is manual.</div>
<div class=vs-yes><b>PyWeb</b><br>signal change → notify exactly the bindings that read it → patch those nodes. O(bindings) work; dependencies are compiler-derived.</div>
</div>
<h2 class=sec>Explicit primitives (advanced)</h2>
<p class=lead>Basic apps never need these, but they exist and the compiler
lowers plain code into them:</p>
<table class=spec>
<tr><th>Primitive</th><th>Use</th></tr>
<tr><td><span class=k>Signal(x)</span></td><td>Explicit mutable state with subscribers</td></tr>
<tr><td><span class=k>Computed(f)</span></td><td>Cached derived value, recomputed when deps change</td></tr>
<tr><td><span class=k>Resource(loader)</span></td><td>Async data with pending / error / result</td></tr>
<tr><td><span class=k>Effect(f)</span></td><td>Side effect re-run on dependency change</td></tr>
</table>
<div class=ok><b>Time-travel:</b> the runtime event log records every signal
transition with timestamps, so DevTools can replay state — possible only
because the compiler owns the reactive graph.</div>
</section>
""",
)

_todo_js = TODO_PAGE["js"]
_todo_html = TODO_PAGE["html_body"]
_todo_rpc = TODO_OUT["rpc"]
_todo_rpc_rows = "".join(
    "<tr><td><span class=k>%s</span></td><td>%s</td><td>%s</td><td>%s</td><td>line %d</td></tr>"
    % (
        html.escape(s["name"]),
        html.escape(", ".join(a["name"] + ": " + a["type"] for a in s["args"]) or "—"),
        html.escape(s["returns"]),
        html.escape(s["location"]),
        s["line"],
    )
    for s in _todo_rpc
)
_todo_place = "<br>".join(
    "<span class=k>%s</span> → <b>%s</b> <span style='color:var(--faint)'>%s</span>"
    % (html.escape(sym), html.escape(loc), html.escape(reason))
    for sym, (loc, reason) in TODO_PAGE["placement"].items()
)

add(
    "rpc.html",
    "One program, two machines: RPC and placement",
    "PyWeb decides where each function runs and generates typed RPC stubs, "
    "so cross-boundary calls feel like local calls.",
    """
<section class=block>
<h2 class=sec>One program, two (or more) machines</h2>
<p class=lead>The placement pass builds symbol, effect, data, and security
graphs, then assigns each function to browser / server / edge / worker /
shared. Secrets, DB drivers, and privileged imports pin their module to
the server — the compiler errors instead of leaking.</p>
"""
    + tabs(
        [
            ("todo app.pyweb", code(TODO_SRC)),
            ("generated RPC stub · %d B" % len(_todo_js.encode()), code(_todo_js)),
            ("SSR HTML", code(_todo_html)),
        ]
    )
    + """
<h2 class=sec>Generated RPC contract</h2>
<p class=lead>Python annotations are the contract — no OpenAPI files, no DTO
duplication. What the compiler derived from the todo app above:</p>
<table class=spec>
<tr><th>Function</th><th>Args</th><th>Returns</th><th>Runs on</th><th>Defined</th></tr>
"""
    + _todo_rpc_rows
    + """
</table>
<h2 class=sec>Placement decisions with reasons</h2>
<p class=lead>Same output as <code>pyweb inspect</code> — every symbol, its
location, and why:</p>
<p class=lead>{place}</p>
<div class=info><b>Failure semantics stay visible:</b> the stub propagates
auth headers, sends CSRF tokens, retries only idempotent calls, honors
timeouts and cancellation, and maps server error codes back to Python
exceptions with source-mapped tracebacks. RPC feels local; it never
<em>lies</em> about being remote.</div>
<div class=warn><b>Safety rule:</b> when the compiler is unsure where code
can safely run, it keeps it on the server. Overrides
(<code>@browser @server @edge @worker @shared</code>) are escape hatches,
not daily syntax.</div>
</section>
""".format(place=_todo_place),
)

add(
    "playground.html",
    "Compiler playground",
    "Step through exactly what PyWeb does to the counter app: parse, "
    "reactivity analysis, placement, RPC, SSR, and browser codegen.",
    """
<section class=block>
<h2 class=sec>Compiler playground</h2>
<p class=lead>A guided tour of the six compiler stages, using the
<b>real artifacts</b> produced for the counter app when this page was
built. Nothing here is a mockup.</p>
<div class=pg-grid>
<div class=pg-step><h4>Stage 1 · Parse</h4><p>Markup inside Python becomes UI
nodes in a unified AST alongside normal statements. The counter yields one
page, one handler, one binding.</p></div>
<div class=pg-step><h4>Stage 2 · Reactivity</h4><p><code>compute_reactive</code>
finds <code>count</code> read by markup and mutated by
<code>increment</code> → signals: <span class=k>{sigs}</span>.</p></div>
<div class=pg-step><h4>Stage 3 · Placement</h4><p>Effect + security graphs
assign every symbol. <code>increment</code> is browser-safe;
<code>__page__</code> is browser+server (SSR + activation).</p></div>
<div class=pg-step><h4>Stage 4 · RPC</h4><p><code>@server</code> functions
become endpoint + typed stub + validation + auth + CSRF + retries. This app
has none — so zero RPC bytes ship.</p></div>
<div class=pg-step><h4>Stage 5 · SSR</h4><p>Initial HTML renders on the server
with binding anchors (<code>data-pw-id</code>), so first paint needs no
JS.</p></div>
<div class=pg-step><h4>Stage 6 · Browser codegen</h4><p>Fine-grained JS: one
signal, one text binding, one event listener. <b>{jsb} bytes</b> of page JS
plus the shared runtime.</p></div>
</div>
""".format(sigs=html.escape(_counter_sigs), jsb=len(_counter_js.encode()))
    + codehead("counter page JS (generated)", len(_counter_js.encode()))
    + code(_counter_js)
    + codehead("counter SSR HTML (generated)", len(_counter_html.encode()))
    + code(_counter_html)
    + """
<p class=lead>Try it locally: <code>python -m pyweb.cli inspect
examples/counter/app.pyweb</code> prints the same analysis in your
terminal.</p>
</section>
""",
)
# --------------------------------------------------------------------------
# Database, auth, realtime, styling, browser APIs, testing
# --------------------------------------------------------------------------

add(
    "database.html",
    "Database: Postgres-first, honest everywhere",
    "Models, parameterized queries, pooling, streaming, retries, and "
    "versioned migrations with a journal and rollback.",
    """
<section class=block>
<h2 class=sec>Postgres-first, honest everywhere</h2>
<p class=lead>Parameterized queries only (values never touch SQL text),
pooling, prepared statements, streaming cursors, transient-error retries,
pagination. <code>connect(DATABASE_URL)</code> opens sqlite / postgres /
mysql. Models are optional — SQLAlchemy and raw SQL keep working.</p>
"""
    + code(
        """class Product(Model):
    name: str
    price: Decimal
    stock: int

cheap = Product.where(stock__gt=0, price__lt=1000).paginate(page=2)

# migrations are versioned + journaled
pyweb db new add_stock_index
pyweb db migrate --database $DATABASE_URL
pyweb db status
pyweb db rollback --steps 1          # revert latest applied
pyweb db rollback --to 0003_add_users  # revert everything after 0003"""
    )
    + """
<table class=spec>
<tr><th>Concern</th><th>Answer</th></tr>
<tr><td>Injection</td><td>Values are always bound parameters; SQL text is built from an allow-list of operators</td></tr>
<tr><td>Pool exhaustion</td><td>Bounded pool, prepared statements, streaming cursors for large scans</td></tr>
<tr><td>Flaky networks</td><td>Transient-error retries with backoff; idempotency keys on writes</td></tr>
<tr><td>Schema drift</td><td>Versioned migrations + append-only journal; <code>status</code> shows applied vs pending; <code>rollback</code> reverts</td></tr>
<tr><td>Live UI</td><td><code>live(Model.where(...))</code> re-queries on notify and pushes minimal patches — see <a href="realtime.html">realtime</a></td></tr>
</table>
<div class=warn><b>No lock-in:</b> Postgres remains Postgres. Migrations emit
plain SQL you can run without PyWeb; the data is never wrapped in a
proprietary store.</div>
</section>
""",
)

add(
    "auth.html",
    "Auth and security",
    "Compiler-aware authentication: signed sessions, RBAC, rotation, magic "
    "links, TOTP, OAuth/OIDC, WebAuthn — plus the security model.",
    """
<section class=block>
<h2 class=sec>Auth is compiler-aware</h2>
<p class=lead>Signed sessions (HttpOnly, SameSite=Lax, Max-Age matched to
the server-side TTL), PBKDF2 passwords, RBAC policies, session rotation,
magic links, TOTP, OAuth/OIDC clients, and WebAuthn verification. Security
boundaries feed placement: auth-gated code stays server-side. In
production behind HTTPS pass <code>secure=True</code> so cookies also
carry <code>Secure</code> — <code>pyweb serve</code> warns at startup when
auth is enabled on a non-local host without it
(<code>PYWEB_COOKIE_SECURE=1</code>).</p>
"""
    + code(
        """@app.page("/dashboard")
@auth.required
def dashboard(): ...

@permission("admin")
def delete_user(uid: int): ...

pyweb check app.pyweb   # secret-leak + vulnerability scan in CI"""
    )
    + """
<div class=ok>XSS (escaped templates) / SQLi (parameterized only) / CSRF
(tokens + SameSite) / uploads (type/size validation + safe paths) / secret
leak (compile error, not warning).</div>
<h2 class=sec>Threat model</h2>
<table class=spec>
<tr><th>Threat</th><th>Mitigation</th><th>Verified by</th></tr>
<tr><td>Secret shipped to browser</td><td>Placement pins secrets server-side; leak is a compile error</td><td><code>pyweb check</code> in CI</td></tr>
<tr><td>Session theft</td><td>Signed, HttpOnly, SameSite=Lax cookies; rotation on privilege change</td><td>auth tests</td></tr>
<tr><td>CSRF</td><td>Per-session tokens on mutations + SameSite cookies</td><td>form tests</td></tr>
<tr><td>Open redirect</td><td>Allow-list validated <code>next</code> parameters</td><td>attack-case tests</td></tr>
<tr><td>Path traversal</td><td><code>safe_join</code> containment on uploads/static</td><td>attack-case tests</td></tr>
<tr><td>SSRF</td><td>Egress allow-list on server fetch</td><td>scan tests</td></tr>
</table>
</section>
""",
)

add(
    "realtime.html",
    "Realtime, jobs, caching, offline",
    "Live queries, background tasks, layered caching, and offline-first sync "
    "without hand-written plumbing.",
    """
<section class=block>
<h2 class=sec>Live data without plumbing</h2>
<p class=lead>Live queries track row dependencies and push minimal patches
(SSE with Last-Event-ID replay, WebSocket where open, polling fallback).
Background <code>@task</code> jobs persist on Redis with retries and
progress; <code>@cache(minutes=5)</code> + SWR covers reads.</p>
"""
    + code(
        """msgs = live(Message.where(room=current_room))

@task
def generate_report(user: User): ...
job = generate_report(user)
if job.pending: <Spinner />

@cache(minutes=5)
def product_list(): ..."""
    )
    + """
<table class=spec>
<tr><th>Primitive</th><th>Semantics</th></tr>
<tr><td><span class=k>live(query)</span></td><td>Server re-runs on notify; client applies minimal patch; reconnect replays from Last-Event-ID</td></tr>
<tr><td><span class=k>@task</span></td><td>Queued, persisted, retried with backoff; progress + cancellation + tracing flow into the request trace</td></tr>
<tr><td><span class=k>@cache</span></td><td>Memory + Redis + CDN layers; tags + SWR; never silently caches correctness-sensitive ops</td></tr>
<tr><td><span class=k>sync models</span></td><td>Offline queue replays on reconnect; last-writer-wins + tombstones + conflict reports; optimistic UI rolls back on failure</td></tr>
</table>
</section>
""",
)

add(
    "styling.html",
    "Styling without a prison",
    "Plain CSS, scoped modules, design tokens, Tailwind bridge, responsive "
    "patterns — the abstraction always unwraps.",
    """
<section class=block>
<h2 class=sec>Easy defaults, no ceiling</h2>
<p class=lead>Four levels, all compiling to real CSS. Advanced developers
never hit an unbreakable abstraction — the page below is styled almost
entirely with the same plain-CSS approach.</p>
"""
    + code(
        """<Button primary>Buy now</Button>            # design-token props
<Button class_="checkout">Buy now</Button>  # your own CSS wins

<Button css={"padding": 12,              # inline tokens
             "border_radius": 8}>

# plain CSS / modules / Tailwind all work
# website/dist/assets/style.css is one hand-auditable file
.checkout { padding: 12px; border-radius: 8px; }"""
    )
    + """
<table class=spec>
<tr><th>Approach</th><th>When</th></tr>
<tr><td>Token props (<code>primary</code>)</td><td>Prototypes and internal tools</td></tr>
<tr><td><code>class_</code> + plain CSS</td><td>Real products; full control, DevTools-friendly</td></tr>
<tr><td>Scoped CSS modules</td><td>Component libraries avoiding collisions</td></tr>
<tr><td>Tailwind bridge</td><td>Utility-first teams; emits standard classes</td></tr>
</table>
<div class=ok><b>Accessibility is structural:</b> components emit semantic
HTML (<code>&lt;article&gt;</code>, labeled inputs, focus management),
templates escape by default, and forms carry accessible errors — not as a
plugin, but as codegen defaults.</div>
</section>
""",
)

add(
    "browser.html",
    "Browser APIs and npm interop",
    "Typed Python bindings for web platform APIs, and typed stubs generated "
    "from TypeScript declarations — PyWeb never reimplements the ecosystem.",
    """
<section class=block>
<h2 class=sec>The web platform, typed</h2>
<p class=lead>Browser capabilities surface as typed Python — async where the
platform is async — so misuse is a compile error, not a runtime surprise:</p>
"""
    + code(
        """from pyweb.browser import storage, clipboard, location, notifications

storage["theme"] = "dark"
await clipboard.write("hello")
position = await location.current()"""
    )
    + """
<p class=lead>Covered: localStorage, IndexedDB, Clipboard, Geolocation,
Notifications, Fetch, Streams, WebSockets, WebRTC, Workers, Service
Workers, Canvas, WebGL/WebGPU, File System, permissions, audio/video.</p>
<h2 class=sec>npm without the cliff</h2>
<p class=lead><code>pyweb npm</code> reads <code>.d.ts</code> files and
generates typed Python bindings; <code>package()</code> pins the importmap
in the SSR shell so versions are reproducible:</p>
"""
    + code(
        """# TypeScript declaration in  →  typed Python out
# interface ChartProps { data: number[]; animated?: boolean }

from npm.chartjs import Chart   # typed props, checked at build time

pyweb npm node_modules/chart.js/dist/chart.d.ts  # generate stubs"""
    )
    + """
<div class=info><b>Architecture note:</b> PyWeb has no React dependency.
Existing React components load through an optional compatibility layer, and
any JS module is reachable through the escape hatch — the abstraction
ladder always goes down to raw DOM, CSS, and JS/TS.</div>
</section>
""",
)

add(
    "testing.html",
    "Testing full-stack apps",
    "Virtual-browser tests for speed, real-browser hooks for fidelity, and "
    "RPC/database tests that understand application structure.",
    """
<section class=block>
<h2 class=sec>Full-stack tests without the browser tax</h2>
<p class=lead>Because PyWeb understands the application graph, most tests run
against the compiled SSR + virtual client — no browser overhead. Reach for
a real browser only for rendering fidelity.</p>
"""
    + code(
        """def test_checkout(app):
    page = app.open("/cart")
    page.click("Checkout")

    assert page.text("Success")
    assert Order.count() == 1"""
    )
    + """
<table class=spec>
<tr><th>Level</th><th>Runs</th><th>For</th></tr>
<tr><td>Component / state</td><td>pytest, in-process</td><td>Reactive logic, validation, placement unit checks</td></tr>
<tr><td>RPC</td><td>pytest, stub transport</td><td>Serialization, auth propagation, error mapping</td></tr>
<tr><td>Virtual browser</td><td><code>pyweb.testing.Client</code></td><td>Full-stack flows without a browser</td></tr>
<tr><td>Real browser</td><td>hooks + traces</td><td>Rendering, focus, a11y spot-checks</td></tr>
<tr><td>E2E + DB</td><td>stdlib harness</td><td>Migrations, fanout, offline replay</td></tr>
</table>
</section>
""",
)
# --------------------------------------------------------------------------
# Production, deploy, benchmarks, API reference
# --------------------------------------------------------------------------

_bench_table_rows = "".join(
    "<tr><td><span class=k>%s</span></td><td>%d</td><td>%s B <span style='color:var(--faint)'>(page %s + runtime %s)</span></td>"
    "<td>%s ms</td><td>%d</td><td>%d</td></tr>"
    % (
        name,
        BENCH[name]["source_lines"],
        f"{BENCH[name]['js_bytes']:,}",
        f"{BENCH[name]['page_js_bytes']:,}",
        f"{BENCH[name]['runtime_js_bytes']:,}",
        BENCH[name]["ssr_ms"],
        BENCH[name]["signals"],
        BENCH[name]["rpc"],
    )
    for name in BENCH
)
_bench_max = max(BENCH[n]["js_bytes"] for n in BENCH)

add(
    "production.html",
    "Operate it like you mean it",
    "Serving, observability, error taxonomy, caching, budgets, and source "
    "maps — production behavior that is measured, not assumed.",
    """
<section class=block>
<h2 class=sec>Operate it like you mean it</h2>
<table class=spec>
<tr><th>Concern</th><th>PyWeb answer</th></tr>
<tr><td>Serving</td><td><code>pyweb serve dist</code>: threaded, immutable asset caching, <code>/healthz</code> (+ <code>/readyz</code>), HEAD, hardened headers (nosniff / same-origin / SAMEORIGIN), 1&nbsp;MiB body cap (413), SIGTERM/SIGINT drain, cookie-parsed sessions via <code>--app mod:factory</code> + <code>PYWEB_AUTH_SECRET</code></td></tr>
<tr><td>Observability</td><td>W3C traces from button → RPC → DB → worker → DOM; error taxonomy with codes; structured logs; metrics; event log</td></tr>
<tr><td>Debugging</td><td>Generated JS carries <code>// pyweb-line:N</code> mappings; runtime errors render against original <code>.pyweb</code> source</td></tr>
<tr><td>Caching</td><td>Memory + Redis, tags, SWR, invalidation that works</td></tr>
<tr><td>Testing</td><td>Virtual-browser client: full-stack tests without a browser; real-browser hooks when needed</td></tr>
<tr><td>Budgets</td><td><code>--budget static/app.js=30kb</code> fails builds that bloat — enforced in CI</td></tr>
<tr><td>npm</td><td><code>package("chart.js@4.4.0")</code> becomes a pinned importmap in the SSR shell + manifest</td></tr>
</table>
<h2 class=sec>Source maps point at Python</h2>
<p class=lead>A browser failure renders like this — never an opaque bundle
stack:</p>
"""
    + code(
        """app.pyweb:41

41 | total = price * quantity
             ^^^^^

Type mismatch: price was None"""
    )
    + """
<h2 class=sec>One trace across the boundary</h2>
<p class=lead>Request IDs (<code>X-Request-Id</code>) and W3C
<code>traceparent</code> propagate through the RPC transport into server
spans, DB query spans, and worker spans — one logical click is traceable
end to end, and the dev server preserves the mapping across hot reloads.</p>
</section>
""",
)

add(
    "deploy.html",
    "Deploy anywhere — no mandatory cloud",
    "Standard artifacts for Docker, compose, Kubernetes, VMs, and static "
    "hosts. Postgres stays Postgres; containers stay containers.",
    """
<section class=block>
<h2 class=sec>No mandatory cloud</h2>
<p class=lead>Standard artifacts: Postgres stays Postgres, containers stay
containers. One command scaffolds what your platform needs.</p>
"""
    + code(
        """pyweb deploy --target docker               # build+serve+HEALTHCHECK
pyweb deploy --target compose --db-url $DATABASE_URL
pyweb deploy --target k8s --image registry/shop:v1  # liveness+readiness

docker build -t shop . && docker run -p 8000:8000 shop
# /healthz answers; probes already wired"""
    )
    + """
<table class=spec>
<tr><th>Target</th><th>You get</th></tr>
<tr><td>Linux VM</td><td><code>pyweb serve dist</code> behind any reverse proxy; systemd unit pattern in docs</td></tr>
<tr><td>Docker</td><td>Generated Dockerfile: build → non-root serve → <code>HEALTHCHECK /healthz</code></td></tr>
<tr><td>Kubernetes</td><td>Deployment + Service with liveness/readiness probes wired to <code>/healthz</code></td></tr>
<tr><td>Static + API split</td><td>Hashed <code>dist/static</code> on any CDN; Python server anywhere</td></tr>
</table>
<div class=ok><b>Zero lock-in rule:</b> every artifact PyWeb emits (SQL,
HTML, JS, containers, manifests) is usable without PyWeb. The framework
adds intelligence; it never takes your data hostage.</div>
</section>
""",
)

add(
    "benchmarks.html",
    "Benchmarks: measured, not assumed",
    "Bundle sizes, SSR latency, and compile times for the reference apps, "
    "regenerated from the real compiler every time this site builds.",
    """
<section class=block>
<h2 class=sec>Benchmarks: measured, not assumed</h2>
<p class=lead>Generated at site build time by <code>python -m
pyweb.bench</code> against the real compiler. Totals <b>include the shared
runtime</b> — per-page code is the small slice. Budgets
(<code>runtime &lt; 12 KB minified</code>) are enforced in CI so these
numbers cannot silently regress.</p>
<p class=stamp><span class=live-dot></span>Live build-time numbers ·
minified shared runtime: {rt:,} B</p>
""".format(rt=RUNTIME_MIN)
    + bench_bars(
        [(n, BENCH[n]["js_bytes"], "tiny" if n == "counter" else "") for n in BENCH],
        unit="B",
        scale=_bench_max,
    )
    + """
<table class=spec>
<tr><th>App</th><th>Source lines</th><th>Shipped JS</th><th>SSR time</th><th>Signals</th><th>RPC fns</th></tr>
"""
    + _bench_table_rows
    + """
</table>
<div class=info><b>Reading these honestly:</b> the runtime (~{rt} KB
minified) is downloaded once and cached across pages. A static page ships
near-zero JS. Compare workloads, not slogans — the harness and baselines
live in-repo at <code>pyweb/bench.py</code>.</div>
</section>
""".format(rt=round(RUNTIME_MIN / 1024, 1)),
)

API_SECTIONS = [
    ("Compiler", "pyweb.compiler", "compile_source(source, filename, route, title) → graph + IR text + RPC specs + per-page artifacts (SSR HTML, page JS, signals, placement, source maps)."),
    ("Reactivity", "pyweb.reactive", "Signal / Computed / Resource / Effect primitives; the compiler lowers plain variables into these automatically."),
    ("RPC", "pyweb.rpc", "Typed cross-boundary stubs: serialization, auth propagation, CSRF, retries, timeouts, error mapping, tracing."),
    ("Routing", "pyweb.app", "App() + @app.page(route, render=...) with typed params, layouts, middleware, redirects, error/loading boundaries, sitemap."),
    ("Models & DB", "pyweb.models", "Model base, where() query builder, pooling, prepared statements, streaming, retries, pagination, journaled migrations."),
    ("Auth", "pyweb.auth", "Signed sessions, PBKDF2 passwords, RBAC policies, rotation, magic links, TOTP, OAuth/OIDC, WebAuthn."),
    ("Realtime", "pyweb.realtime", "Channels, rooms, presence, pub/sub over SSE/WebSocket with polling fallback."),
    ("Jobs & cache", "pyweb.jobs + pyweb.cache", "Persisted @task workers with retries/progress; memory+Redis @cache with tags + SWR."),
    ("Offline sync", "pyweb.sync", "Queued mutations, reconnect replay, LWW + tombstones, conflict reports, optimistic UI."),
    ("Browser APIs", "pyweb.browser", "Typed storage/clipboard/location/notifications/fetch/streams/workers/canvas bindings."),
    ("Styling", "pyweb.css", "Scoped modules, design tokens, Tailwind bridge emitting standard CSS."),
    ("Forms", "pyweb.forms", "Model-driven forms with shared frontend/server validation, CSRF, uploads."),
    ("npm interop", "pyweb.npm", "package() pins + .d.ts → typed Python stub generation."),
    ("Security", "pyweb.security", "escape/ssr/safe_join/safe_redirect/scan/check_source — the check gate."),
    ("Observability", "pyweb.observability", "Logger/Tracer/Metrics, error taxonomy + format_error with source spans, event log."),
    ("Testing", "pyweb.testing", "Virtual-browser Client/Page: open/click/fill/text/rpc without a browser."),
    ("Build", "pyweb.build", "Production builds: hashing, minification, splitting, importmap, manifest, budgets."),
    ("Deploy", "pyweb.deploy", "docker/compose/k8s artifact generation with healthchecks and probes."),
    ("DevTools", "pyweb.lsp + dev server", "Inspect data, language-server completions, error overlay, time-travel log."),
    ("Plugins", "pyweb.plugins", "Typed plugin surface participating in the application graph."),
    ("Platforms", "pyweb.platform", "Web/mobile/desktop target abstraction with platform-conditional code."),
]

_api_rows = "".join(
    "<tr><td>%s</td><td><span class=k>%s</span></td><td>%s</td></tr>" % (t, m, d)
    for t, m, d in API_SECTIONS
)

add(
    "api.html",
    "API reference map",
    "Every module, what it owns, and which guide explains it — a single map "
    "from concept to code.",
    """
<section class=block>
<h2 class=sec>API reference map</h2>
<p class=lead>PyWeb ships as focused modules, not one god object. Install
<code>pip install pyweb</code> for everything, or depend on individual
packages as they stabilize.</p>
<table class=spec>
<tr><th>Area</th><th>Module</th><th>Owns</th></tr>
"""
    + _api_rows
    + """
</table>
<p class=lead>Authoritative signatures live in the source
(<code>pyweb/*.py</code>) with docstrings; <code>ARCHITECTURE.md</code> is
the full design document and <code>docs/BUGLOG.md</code> logs every bug
found and fixed.</p>
</section>
""",
)
# --------------------------------------------------------------------------
# Examples gallery, search, roadmap, build()
# --------------------------------------------------------------------------

_gallery_cards = []
for _app in ("counter", "todo", "blog", "auth", "chat", "showcase"):
    _src, _out, _pg = EXAMPLES[_app]
    _sigs = len(_pg["signals"])
    _jsb = len(_pg["js"].encode())
    _rpcn = len(_out["rpc"])
    _gallery_cards.append(
        "<div class=card><h3>%s</h3><p>%d lines · %d page(s) · %d signal(s) · "
        "%d B page JS · %d RPC fn(s)</p>"
        "<p style='margin-top:10px'><a href='example-%s.html'>Source + compiled output →</a></p></div>"
        % (_app, len(_src.splitlines()), len(_out["pages"]), _sigs, _jsb, _rpcn, _app)
    )

add(
    "examples.html",
    "Example applications",
    "Every reference app with its source, compiled output, and size stats — "
    "all generated from the real compiler at build time.",
    """
<section class=block>
<h2 class=sec>Example applications</h2>
<p class=lead>Each example compiles, builds, and passes <code>pyweb
check</code> in CI. Open one to see its source alongside the actual JS,
HTML, and placement the compiler produces.</p>
<div class=grid3>
"""
    + "".join(_gallery_cards)
    + """
</div></section>
""",
)

for _app in ("counter", "todo", "blog", "auth", "chat", "showcase"):
    _src, _out, _pg = EXAMPLES[_app]
    _place_rows = "<br>".join(
        "<span class=k>%s</span> → <b>%s</b> <span style='color:var(--faint)'>%s</span>"
        % (html.escape(s), html.escape(l), html.escape(r))
        for s, (l, r) in _pg["placement"].items()
    )
    add(
        "example-%s.html" % _app,
        "Example: %s" % _app,
        "Source, compiled JS, SSR HTML, and placement for the %s reference app." % _app,
        """
<section class=block>
<h2 class=sec>Example: {app}</h2>
<p class=lead>{lines} lines · {sigs} signal(s) · {rpc} RPC function(s) ·
route <span class=k>{route}</span></p>
""".format(
            app=html.escape(_app),
            lines=len(_src.splitlines()),
            sigs=len(_pg["signals"]),
            rpc=len(_out["rpc"]),
            route=html.escape(_pg["route"]),
        )
        + tabs(
            [
                ("app.pyweb", code(_src)),
                ("compiled JS · %d B" % len(_pg["js"].encode()), code(_pg["js"])),
                ("SSR HTML", code(_pg["html_body"])),
            ]
        )
        + """
<p class=lead style="margin-top:14px">Placement:<br>{place}</p>
<p class=lead><a href="examples.html">← all examples</a></p>
</section>
""".format(place=_place_rows),
        active="examples.html",
    )

add(
    "search.html",
    "Search docs",
    "Full-text search across every docs page.",
    """
<section class=block>
<h2 class=sec>Search docs</h2>
<p class=lead>Type below — results rank title matches first. (Static index,
no tracking, no server.)</p>
<p><input id="q" type="search" placeholder="e.g. migrations, CSRF, placement…" aria-label="Search docs" class="w-full max-w-[480px] rounded-xl border border-line bg-panel px-3.5 py-2.5 text-[15px] text-ink placeholder:text-faint"></p>
<div id="results"></div>
<script>
var IDX = __INDEX__;
function run(q){
  q = q.toLowerCase();
  var box = document.getElementById("results");
  if(!q){ box.innerHTML = "<p class=lead>Type to search " + IDX.length + " pages.</p>"; return; }
  var scored = IDX.map(function(p){
    var t = (p.title + " " + p.text).toLowerCase();
    var ti = p.title.toLowerCase().indexOf(q);
    var bi = t.indexOf(q);
    if(bi < 0) return null;
    return {p: p, s: (ti >= 0 ? 0 : 10000) + bi};
  }).filter(Boolean).sort(function(a, b){ return a.s - b.s; }).slice(0, 20);
  box.innerHTML = scored.length
    ? "<table class=spec>" + scored.map(function(r){
        return "<tr><td><a href='" + r.p.name + "'>" + r.p.title + "</a></td><td>" + r.p.text.slice(Math.max(0, r.p.text.toLowerCase().indexOf(q) - 80), r.p.text.toLowerCase().indexOf(q) + 160).replace(/</g, "&lt;") + "…</td></tr>";
      }).join("") + "</table>"
    : "<p class=lead>No matches. Try 'RPC', 'auth', 'deploy', 'offline'…</p>";
}
document.addEventListener("DOMContentLoaded", function(){
  var m = /[?&]q=([^&]+)/.exec(location.search);
  var q = document.getElementById("q");
  if(m){ q.value = decodeURIComponent(m[1].replace(/\\+/g, " ")); }
  q.addEventListener("input", function(){ run(q.value.trim()); });
  run(q.value.trim());
});
</script>
</section>
""",
)

add(
    "roadmap.html",
    "Roadmap: what is real",
    "An honest map of what PyWeb v1 delivers, what is preview, and what is "
    "future — no vaporware.",
    """
<section class=block>
<h2 class=sec>Honest roadmap</h2>
<table class=spec>
<tr><th>Status</th><th>Area</th></tr>
<tr><td><span class="pill real">real</span></td><td>Reactivity, RPC, placement, SSR,
routing, Postgres/MySQL/SQLite + journaled migrations, auth/RBAC, realtime
bus, jobs, cache, offline sync core, hashed builds, serve + health,
importmap, inspect, tests</td></tr>
<tr><td><span class="pill preview">preview</span></td><td>Mobile/desktop targets (AST
pruning works, native emitters partial), Tailwind bridge, plugin SDK
surface</td></tr>
<tr><td><span class="pill future">future</span></td><td>Streaming SSR, WASM browser
target, multi-node CRDT sync, managed cloud offering</td></tr>
</table>
<div class=warn><b>Rule:</b> uncertain code stays on the server. The compiler
optimizes only when it can prove safety — correctness first, always.</div>
<h2 class=sec>How to follow along</h2>
<ul class=check>
<li>Read <code>ARCHITECTURE.md</code> for the full design</li>
<li>Run <code>python -m pyweb.bench</code> to reproduce every number on this site</li>
<li>Open an issue or PR at <a href="https://github.com/MaanavKrishna/PyWeb">github.com/MaanavKrishna/PyWeb</a></li>
</ul>
</section>
""",
)

add(
    "404.html",
    "Not found",
    "The page you asked for does not exist.",
    """
<section class=block>
<h2 class=sec>Lost in the layers?</h2>
<p class=lead>This URL has no page. Try <a href="index.html">home</a>,
<a href="search.html">search</a>, or the <a href="guide.html">5-minute
guide</a>.</p>
</section>
""",
)


def build():
    os.makedirs(os.path.join(OUT, "assets"), exist_ok=True)
    with open(os.path.join(OUT, "assets", "style.css"), "w") as fh:
        fh.write(CSS)
    with open(os.path.join(OUT, "assets", "site.js"), "w") as fh:
        fh.write(JS)
    # Render content pages first: page() populates SEARCH_TEXTS as a
    # side effect, so the search index must be built after, not before.
    for name, (title, desc, body, active, wide) in PAGES.items():
        if name == "search.html":
            continue
        with open(os.path.join(OUT, name), "w") as fh:
            fh.write(page(name, title, desc, body, active, wide))
    index = [
        {"name": name, "title": entry["title"], "text": entry["text"]}
        for name, entry in SEARCH_TEXTS.items()
    ]
    title, desc, body, active, wide = PAGES["search.html"]
    body = body.replace("__INDEX__", json.dumps(index))
    with open(os.path.join(OUT, "search.html"), "w") as fh:
        fh.write(page("search.html", title, desc, body, active, wide))
    with open(os.path.join(OUT, "search_index.json"), "w") as fh:
        json.dump(index, fh)
    urls = "".join(
        "  <url><loc>%s/%s</loc></url>\n" % (SITE, n) for n in sorted(PAGES)
    )
    with open(os.path.join(OUT, "sitemap.xml"), "w") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % urls)
    print(f"built {len(PAGES)} pages -> {OUT}/")


if __name__ == "__main__":
    build()
