// <ErrorBoundary>: when code inside a boundary throws while handling an event (a click, an input,
// what it changes), that part of the page is swapped for its fallback instead of breaking silently.
// Loaded only by pages that use one. The runtime is untouched: the boundary is found from the
// element the event happened on, recorded before any handler runs.
let lastTarget = null;
const remember = (e) => { lastTarget = e.target; };
for (const type of ["click", "input", "change", "submit", "keydown", "keyup", "pointerdown", "focusin", "dragend", "drop"]) {
  document.addEventListener(type, remember, true);
}

/** The children of a boundary element: rendered by `fn`, or nothing (and the fallback shown) if that
 * throws, or if the server already rendered the fallback. */
export function guard(element, fn) {
  return () => {
    const box = element();
    if (box && box.hasAttribute("data-pw-failed")) return [];
    try {
      return fn();
    } catch (error) {
      queueMicrotask(() => fail(box, error));
      return [];
    }
  };
}

function fail(box, error) {
  if (!box || box.hasAttribute("data-pw-failed") && box.querySelector(".pw-boundary-fallback button")) return false;
  box.setAttribute("data-pw-failed", "");
  for (const child of Array.from(box.children)) child.hidden = true;     // the server's fallback too: rebuilt below
  const alert = document.createElement("div");
  alert.setAttribute("role", "alert");
  alert.className = "pw-boundary-fallback";
  const text = document.createElement("p");
  text.textContent = box.getAttribute("data-fallback") || "Something went wrong here.";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "Reload";
  retry.addEventListener("click", () => location.reload());
  alert.append(text, retry);
  box.append(alert);
  console.error("PyWeb: an error inside <ErrorBoundary> was caught", error);
  return true;
}

// The runtime catches errors in event handlers (and in what they change) and announces them.
const boxOf = () => (lastTarget && lastTarget.closest ? lastTarget.closest("[data-pw-boundary]") : null);
window.addEventListener("pyweb:error", (e) => { fail(boxOf(), e.detail); });
window.addEventListener("error", (e) => { if (fail(boxOf(), e.error || e.message)) e.preventDefault(); });
