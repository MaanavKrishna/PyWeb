"""AI help in ``pyweb dev``: "Fix with AI" on the error page and "Edit with AI" on any element.

Who answers, in order:

1. the assistant connected through ``pyweb mcp``, when it supports MCP sampling (its own model does
   the work, and your client asks you before each request);
2. an API key in the environment: ``ANTHROPIC_API_KEY``, or ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``
   (any OpenAI-compatible server), with ``AI_MODEL`` to pick the model;
3. nobody: the page offers the prompt to copy into any assistant, and ``pyweb mcp`` lists the request
   (``pyweb_assist_requests``) so a connected assistant can do it with its own file tools.

The answer is a whole new version of one file in the app. It is compiled before you see it, shown
as a diff, and written only when you press Apply (atomically, and only if the file hasn't changed
since); the old version goes to ``.pyweb/history/`` and Undo puts it back.

What is sent: the file being fixed or edited and the error. Credentials found in them (the same
patterns ``pyweb check`` looks for) are replaced by placeholders before sending and put back in the
answer. Only ``.pyweb`` files inside the app folder can be changed. Everything here is dev-only: the
endpoints exist in ``pyweb dev`` alone and answer requests from this machine only.
"""

from __future__ import annotations

import collections
import difflib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.request

MAX_FILE = 200_000
TIMEOUT = 180

SYSTEM = (
    "You are an expert PyWeb developer. PyWeb apps are .pyweb files: Python plus HTML-like markup "
    "statements inside page and component functions (state is plain Python variables; event handlers "
    "are Python functions; @server functions run on the server). You change exactly one file. Reply "
    "with the complete new contents of that file in a single ```pyweb fenced block, then at most two "
    "sentences saying what you changed. Keep everything you weren't asked to change exactly as it is, "
    "comments and formatting included. Text like <redacted-1> stands for a value that was removed "
    "before sending: keep those placeholders exactly where they are.")


# ------------------------------------------------------------------ errors to fix

_ERRORS = collections.deque(maxlen=20)        # runtime errors from dev pages: {id, text, file, line}


def note_exception(exc, request_id=None):
    """Remember an exception a dev page hit (the debug 500 page), for "Fix with AI"."""
    import traceback

    from .compiler.errors import _is_app_frame, explain_exception
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if _is_app_frame(f.filename)]
    pyweb_frames = [f for f in frames if f.filename.endswith(".pyweb")]
    where = (pyweb_frames or frames or [None])[-1]
    _ERRORS.append({"id": request_id, "text": explain_exception(exc)[:12000],
                    "file": where.filename if where else None, "line": where.lineno if where else None})


def last_exception(request_id=None):
    for item in reversed(_ERRORS):
        if request_id is None or item["id"] == request_id:
            return item
    return None


# ------------------------------------------------------------------ secrets out, secrets back

def redact(text, table):
    """``text`` with credentials replaced by ``<redacted-N>`` (``table`` maps them back)."""
    from .secret_scan import ASSIGN, PATTERNS, PLACEHOLDER, SECRET_NAME, entropy

    def keep(value):
        for k, v in table.items():
            if v == value:
                return k
        key = f"<redacted-{len(table) + 1}>"
        table[key] = value
        return key
    for _label, pattern in PATTERNS:
        text = pattern.sub(lambda m: keep(m.group(0)), text)

    def assign(m):
        name, value = m.group("name"), m.group("value")
        if (SECRET_NAME.search(name.rsplit(".", 1)[-1]) and not PLACEHOLDER.search(value)
                and entropy(value) >= 3.5 and not value.startswith(("http://", "https://", "/"))):
            return m.group(0).replace(value, keep(value))
        return m.group(0)
    return ASSIGN.sub(assign, text)


def unredact(text, table):
    for key, value in table.items():
        text = text.replace(key, value)
    return text


# ------------------------------------------------------------------ who answers

class _Sampling:
    """Jobs for the assistant connected through ``pyweb mcp`` (it long-polls the dev server)."""

    def __init__(self):
        self.jobs = collections.OrderedDict()     # id -> {"system", "user", "event", "answer", "taken"}
        self.seen = 0.0                            # when a sampling client last asked for work
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)

    def alive(self):
        return time.time() - self.seen < 45

    def ask(self, system, user, timeout=TIMEOUT):
        job = {"id": secrets.token_hex(6), "system": system, "user": user, "event": threading.Event(),
               "answer": None, "taken": False}
        with self.wake:
            self.jobs[job["id"]] = job
            self.wake.notify_all()
        try:
            if not job["event"].wait(timeout):
                raise AssistError("the assistant didn't answer in time")
        finally:
            with self.lock:
                self.jobs.pop(job["id"], None)
        if job["answer"].get("error"):
            raise AssistError(f"the assistant said: {job['answer']['error']}")
        return job["answer"].get("text") or ""

    def next(self, wait=5.0):
        """The next job for the MCP side (marks the client alive), or None after ``wait`` seconds."""
        end = time.time() + wait
        with self.wake:
            while True:
                self.seen = time.time()
                job = next((j for j in self.jobs.values() if not j["taken"]), None)
                if job is not None:
                    job["taken"] = True
                    return {"id": job["id"], "system": job["system"], "user": job["user"]}
                left = end - time.time()
                if left <= 0:
                    return None
                self.wake.wait(min(left, 5.0))

    def answer(self, job_id, data):
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return False
        job["answer"] = data if isinstance(data, dict) else {"text": str(data)}
        job["event"].set()
        return True


SAMPLING = _Sampling()


class AssistError(Exception):
    pass


def provider():
    """``"fake"``, ``"assistant"`` (MCP sampling), ``"anthropic"``, ``"openai"`` or None."""
    if os.environ.get("PYWEB_AI_FAKE"):
        return "fake"
    if SAMPLING.alive():
        return "assistant"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
        return "openai"
    return None


def describe_provider():
    name = provider()
    model = os.environ.get("AI_MODEL")
    return {"fake": "a test answer (PYWEB_AI_FAKE)", "assistant": "your assistant, through pyweb mcp",
            "anthropic": f"Anthropic ({model or 'claude-sonnet-5-5'})", "openai": f"OpenAI-compatible ({model or '?'})",
            None: None}[name]


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AssistError(f"the model API answered {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssistError(f"couldn't reach the model API: {exc}") from None


def complete(system, user):
    """Ask whoever answers (see :func:`provider`); returns the reply text."""
    name = provider()
    if name == "fake":
        with open(os.environ["PYWEB_AI_FAKE"], encoding="utf-8") as fh:
            return fh.read()
    if name == "assistant":
        return SAMPLING.ask(system, user)
    if name == "anthropic":
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                     {"model": os.environ.get("AI_MODEL", "claude-sonnet-5-5"), "max_tokens": 16000,
                      "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if name == "openai":
        model = os.environ.get("AI_MODEL")
        if not model:
            raise AssistError("set AI_MODEL to the model name to use")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        key = os.environ.get("OPENAI_API_KEY")
        data = _post(base + "/chat/completions", {"Authorization": f"Bearer {key}"} if key else {},
                     {"model": model, "messages": [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}]})
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    raise AssistError("no AI is set up")


# ------------------------------------------------------------------ prompts

def _numbered(text, around=None, span=40):
    lines = text.splitlines()
    lo, hi = (1, len(lines)) if around is None else (max(1, around - span), min(len(lines), around + span))
    return "\n".join(f"{n:>5} | {lines[n - 1]}" for n in range(lo, hi + 1))


def fix_prompt(rel, text, error):
    return (f"This PyWeb app file, {rel}, has an error. Fix it.\n\nThe error:\n{error}\n\n"
            f"The file:\n```pyweb\n{text.rstrip(chr(10))}\n```")


def edit_prompt(rel, text, line, instruction):
    return (f"In the PyWeb app file {rel}, change the element that starts on line {line} (and what's inside "
            f"it) as asked: {instruction}\n\nAround line {line}:\n{_numbered(text, line, 12)}\n\n"
            f"The whole file:\n```pyweb\n{text.rstrip(chr(10))}\n```")


_FENCE = re.compile(r"```[ \t]*([\w+-]*)[ \t]*\n(.*?)```", re.S)


def extract(reply):
    """The file in a reply: the longest fenced block (a pyweb/python one preferred), else the reply."""
    blocks = _FENCE.findall(reply or "")
    if not blocks:
        return (reply or "").strip("\n") + "\n"
    best = [b for lang, b in blocks if lang.lower() in ("pyweb", "python", "py", "")] or [b for _, b in blocks]
    return max(best, key=len)


def summary(reply):
    rest = _FENCE.sub("", reply or "").strip()
    return rest[:400]


# ------------------------------------------------------------------ proposals: diff, apply, undo

class Proposals:
    def __init__(self, app_dir):
        self.app_dir = os.path.realpath(app_dir)
        self.items = collections.OrderedDict()
        self.lock = threading.Lock()

    def target(self, file):
        """The absolute path of an app ``.pyweb`` file (``file`` relative to the app or absolute), or raise."""
        from .devopen import resolve
        hit = resolve(f"{file}:1", self.app_dir) if file else None
        if hit is None or not hit[2]:
            raise AssistError("only .pyweb files in this app can be changed")
        return hit[0]

    def propose(self, path, new_text, note="", prompt=""):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        problem = validate(new_text, path)
        rel = os.path.relpath(path, self.app_dir)
        diff = "".join(difflib.unified_diff(old.splitlines(True), new_text.splitlines(True), f"a/{rel}", f"b/{rel}"))
        item = {"id": secrets.token_hex(8), "path": path, "file": rel, "old": old, "new": new_text,
                "mtime": os.path.getmtime(path), "diff": diff, "note": note, "problem": problem,
                "changed": old != new_text}
        with self.lock:
            self.items[item["id"]] = item
            while len(self.items) > 20:
                self.items.popitem(last=False)
        return public(item)

    def apply(self, pid):
        with self.lock:
            item = self.items.get(pid)
        if item is None:
            raise AssistError("that change is gone; ask again")
        if item["problem"]:
            raise AssistError("that change doesn't compile, so it wasn't applied")
        with open(item["path"], encoding="utf-8") as fh:
            now = fh.read()
        if now != item["old"]:
            raise AssistError(f"{item['file']} changed since; ask again")
        keep = history_save(self.app_dir, item["path"], item["old"])
        write(item["path"], item["new"])
        item["history"] = keep
        return {"ok": True, "file": item["file"], "undo": pid}

    def undo(self, pid):
        with self.lock:
            item = self.items.get(pid)
        if item is None or not item.get("history"):
            raise AssistError("nothing to undo")
        with open(item["path"], encoding="utf-8") as fh:
            now = fh.read()
        if now != item["new"]:
            raise AssistError(f"{item['file']} changed after the AI change; undo it by hand "
                              f"(the old version is in {os.path.relpath(item['history'], self.app_dir)})")
        write(item["path"], item["old"])
        item.pop("history")
        return {"ok": True, "file": item["file"]}


def public(item):
    return {k: item[k] for k in ("id", "file", "diff", "note", "problem", "changed")}


def validate(text, path):
    """None when ``text`` compiles as the file at ``path``, else the error message."""
    from .compiler import compile_source
    from .compiler.errors import pretty
    try:
        compile_source(text, filename=path)
    except Exception as exc:  # noqa: BLE001 - shown next to the diff
        return pretty(exc, text, path)
    return None


def write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".pyweb.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def history_save(app_dir, path, text):
    folder = os.path.join(app_dir, ".pyweb", "history")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(app_dir, ".pyweb", ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as fh:
            fh.write("*\n")
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{secrets.token_hex(2)}-" + os.path.basename(path)
    out = os.path.join(folder, name)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    kept = sorted(os.listdir(folder))
    for old in kept[:-50]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return out


# ------------------------------------------------------------------ requests nobody answered automatically

PENDING = collections.deque(maxlen=20)      # for assistants without sampling: pyweb_assist_requests


def ask_for(kind, path, rel, text, user_prompt, proposals):
    """Run one fix or edit: redact, ask, put secrets back, check and keep the proposal."""
    if len(text) > MAX_FILE:
        raise AssistError(f"{rel} is too big to send ({len(text)} characters)")
    table = {}
    prompt = redact(user_prompt, table)
    if provider() is None:
        PENDING.append({"id": secrets.token_hex(6), "kind": kind, "file": rel, "time": time.time(),
                        "request": unredact(prompt, {k: "<redacted>" for k in table})})
        return {"provider": None, "prompt": SYSTEM + "\n\n" + prompt,
                "hint": "No AI is set up. Copy the prompt into any assistant, connect one with `pyweb mcp`, or set "
                        "ANTHROPIC_API_KEY / OPENAI_API_KEY and restart pyweb dev."}
    reply = complete(SYSTEM, prompt)
    new_text = unredact(extract(reply), table)
    if not new_text.endswith("\n"):
        new_text += "\n"
    out = proposals.propose(path, new_text, note=unredact(summary(reply), table))
    out["provider"] = describe_provider()
    return out
