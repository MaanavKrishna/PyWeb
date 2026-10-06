// Passkeys for the PyWeb auth kit's pages (login and account). Loaded only there.

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));

function csrf() {
  const el = document.querySelector('input[name="__pw_csrf"]');
  return el ? el.value : "";
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-PW-CSRF": csrf() },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}

function say(text, kind) {
  const p = document.querySelector("[data-pw-passkey-status]");
  if (!p) return;
  p.textContent = text || "";
  p.hidden = !text;
  p.className = "pw-notice pw-" + (kind || "error");
}

function supported() {
  return typeof window.PublicKeyCredential !== "undefined" && navigator.credentials;
}

// The autofill (conditional) request waits in the background; a click on the button must
// cancel it first, or the browser refuses the second request.
let pending = null;

async function signIn(mediation) {
  if (pending) pending.abort();
  const ctrl = new AbortController();
  pending = ctrl;
  const next = new URLSearchParams(location.search).get("next") || "";
  const opts = await post("/auth/passkey/login/options", {});
  const publicKey = { ...opts.publicKey, challenge: unb64(opts.publicKey.challenge) };
  const cred = await navigator.credentials.get({ publicKey, mediation, signal: ctrl.signal });
  if (pending === ctrl) pending = null;
  if (!cred) return;
  const r = cred.response;
  const data = await post("/auth/passkey/login", {
    id: cred.id, rawId: b64(cred.rawId), next,
    authenticatorData: b64(r.authenticatorData), clientDataJSON: b64(r.clientDataJSON),
    signature: b64(r.signature), userHandle: r.userHandle ? b64(r.userHandle) : null,
  });
  location.assign(data.redirect || "/");
}

async function addPasskey(button) {
  const opts = await post("/auth/passkey/register/options", {});
  const pk = opts.publicKey;
  const publicKey = {
    ...pk, challenge: unb64(pk.challenge), user: { ...pk.user, id: unb64(pk.user.id) },
    excludeCredentials: (pk.excludeCredentials || []).map((c) => ({ ...c, id: unb64(c.id) })),
  };
  const cred = await navigator.credentials.create({ publicKey });
  const nameInput = document.querySelector('input[name="passkey_name"]');
  await post("/auth/passkey/register", {
    id: cred.id, attestationObject: b64(cred.response.attestationObject),
    clientDataJSON: b64(cred.response.clientDataJSON), name: nameInput ? nameInput.value : "",
  });
  location.reload();
}

function start() {
  for (const el of document.querySelectorAll("[data-pw-passkey-only]")) el.hidden = !supported();
  if (!supported()) return;
  for (const btn of document.querySelectorAll("[data-pw-passkey-login]")) {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      signIn(undefined).catch((e) => { if (e.name !== "NotAllowedError" && e.name !== "AbortError") say(e.message); });
    });
  }
  for (const btn of document.querySelectorAll("[data-pw-passkey-add]")) {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      addPasskey(btn).catch((e) => say(e.name === "NotAllowedError" ? "Cancelled." : e.message));
    });
  }
  // Offer saved passkeys in the email field's autofill, where the browser supports it.
  if (document.querySelector('input[autocomplete~="webauthn"]') && PublicKeyCredential.isConditionalMediationAvailable) {
    PublicKeyCredential.isConditionalMediationAvailable().then((ok) => {
      if (ok) signIn("conditional").catch(() => {});
    });
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
