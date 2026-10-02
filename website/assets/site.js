// PyWeb site: theme toggle, mobile menu, copy buttons, tabs, TOC highlight, search.
(function () {
  const root = document.documentElement;

  const theme = document.getElementById("theme");
  if (theme) {
    theme.addEventListener("click", () => {
      const dark = root.dataset.theme
        ? root.dataset.theme === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("pyweb-theme", root.dataset.theme); } catch (e) { /* private mode */ }
    });
  }

  const menu = document.getElementById("menu");
  const side = document.getElementById("side");
  if (menu && side) menu.addEventListener("click", () => side.classList.toggle("open"));

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".copy");
    if (!btn) return;
    const code = btn.closest(".code").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = "Copy"; }, 1400);
    });
  });

  document.querySelectorAll(".tabs").forEach((tabs) => {
    const buttons = tabs.querySelectorAll('[role="tab"]');
    const panes = tabs.querySelectorAll(".pane");
    buttons.forEach((b, i) => b.addEventListener("click", () => {
      buttons.forEach((x, j) => x.setAttribute("aria-selected", String(i === j)));
      panes.forEach((p, j) => { p.hidden = i !== j; });
    }));
  });

  const tocLinks = Array.from(document.querySelectorAll(".toc a"));
  if (tocLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(tocLinks.map((a) => [a.getAttribute("href").slice(1), a]));
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          tocLinks.forEach((a) => a.classList.remove("active"));
          const a = byId.get(en.target.id);
          if (a) a.classList.add("active");
        }
      });
    }, { rootMargin: "-70px 0px -70% 0px" });
    byId.forEach((_a, id) => { const h = document.getElementById(id); if (h) obs.observe(h); });
  }

  const input = document.getElementById("search");
  const results = document.getElementById("results");
  let index = null;
  let active = -1;
  async function load() {
    if (index) return index;
    try { index = await (await fetch("search.json")).json(); } catch (e) { index = []; }
    return index;
  }
  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function render(items) {
    active = -1;
    if (!items.length) { results.innerHTML = '<div class="empty">No results</div>'; results.hidden = false; return; }
    results.innerHTML = items.map((r) => `<a href="${r.url}">${escapeHtml(r.label)}<small>${escapeHtml(r.where)}</small></a>`).join("");
    results.hidden = false;
  }
  async function search(q) {
    q = q.trim().toLowerCase();
    if (q.length < 2) { results.hidden = true; return; }
    const words = q.split(/\s+/);
    const out = [];
    for (const page of await load()) {
      const hay = (page.title + " " + page.text).toLowerCase();
      if (!words.every((w) => hay.includes(w))) continue;
      let score = words.reduce((s, w) => s + (page.title.toLowerCase().includes(w) ? 10 : 1), 0);
      out.push({ url: page.url, label: page.title, where: page.group, score });
      for (const [anchor, heading] of page.headings) {
        if (words.every((w) => heading.toLowerCase().includes(w))) {
          out.push({ url: `${page.url}#${anchor}`, label: heading, where: page.title, score: score + 5 });
        }
      }
    }
    out.sort((a, b) => b.score - a.score);
    render(out.slice(0, 12));
  }
  if (input && results) {
    input.addEventListener("input", () => search(input.value));
    input.addEventListener("keydown", (e) => {
      const links = Array.from(results.querySelectorAll("a"));
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach((a, i) => a.classList.toggle("active", i === active));
      } else if (e.key === "Enter" && links.length) {
        location.href = (links[active] || links[0]).getAttribute("href");
      } else if (e.key === "Escape") {
        results.hidden = true;
        input.blur();
      }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) results.hidden = true; });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement !== input && !e.target.closest("input, textarea")) {
        e.preventDefault();
        input.focus();
      }
    });
  }
})();
