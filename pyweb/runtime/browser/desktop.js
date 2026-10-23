// PyWeb desktop app: links to other sites open in the user's browser, never in the app's window.
// (The window only ever shows the app itself; the server opens the link, http(s) and mailto only.)
// Same-site links that ask for a new tab open in the window instead: the app has one window.

function external(url) {
  return url.origin !== location.origin || !/^https?:$/.test(url.protocol);
}

document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0) return;
  const a = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!a || a.hasAttribute("download")) return;
  let url;
  try {
    url = new URL(a.getAttribute("href"), location.href);
  } catch (_) {
    return;
  }
  if (external(url)) {
    event.preventDefault();
    fetch("/__pyweb/desktop/open", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.href }),
    }).catch(() => {});
  } else if (a.target && a.target !== "_self") {
    event.preventDefault();
    location.assign(url.href);
  }
}, true);

document.documentElement.dataset.pwDesktop = "1";

// Tell the app the window shows a page (the window's checks, like `pyweb desktop --smoke`, use it).
fetch("/__pyweb/desktop/ready", {
  method: "POST",
  credentials: "same-origin",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ title: document.title, path: location.pathname, root: document.querySelector("[data-pw-root]") !== null }),
}).catch(() => {});
