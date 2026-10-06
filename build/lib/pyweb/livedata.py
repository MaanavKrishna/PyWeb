"""Live data: page variables that follow the database.

``rows = live(db, "select ... from todos where done = ?", (False,))`` in a
page (or layout) runs the query while the page renders, like
``db.execute(...).dicts()``. In the browser ``rows`` is reactive: when any
table the query reads is written, the query runs again on the server and
every open page showing it receives the new rows.

How it works:

* Writes through :mod:`pyweb.db` (``INSERT``/``UPDATE``/``DELETE``/...)
  announce the table they changed on the realtime bus, after the
  transaction commits. ``db.notify("table")`` announces writes made some
  other way (another program, raw driver calls).
* Each distinct query (database, SQL, parameters) is registered once per
  process. A watcher thread notices table announcements (instantly in
  the same process, by polling the bus otherwise), waits a moment so a
  burst of writes costs one re-run, re-runs each affected query once, and
  publishes the rows only if they changed. All viewers share that run.
* Pages receive a signed feed for the query's channel (like
  :func:`pyweb.channel`) plus a signed description of the query, so a
  process that didn't render the page (another worker behind a load
  balancer, or one restarted since) can take over re-running it. With
  :class:`pyweb.realtime.RedisBus` this works across processes and hosts.
* When rows have a key (``id``, or ``key=``), a change is sent as a patch:
  ``{"version", "prev", "ops"}`` with deletes, updates, inserts at an index
  and, when rows moved, the new order. One changed row costs one row on the
  wire, and the browser keeps the other rows' DOM. A browser whose version
  isn't ``prev`` (it missed a message) asks for the whole result again.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import weakref

TABLE_CHANNEL = "pyweb.db:"     # + <database id>:<table>
LIVE_CHANNEL = "pyweb.live:"    # + <query key>
IDLE_SECONDS = 600              # forget a query this long after it was last rendered or watched
DEBOUNCE_SECONDS = 0.05
TICK_SECONDS = 0.5
MAX_ROWS = 10_000

_enabled = False
_databases: "weakref.WeakValueDictionary[str, object]" = weakref.WeakValueDictionary()


def enable():
    """Start announcing table writes (done automatically when an app uses ``live()``)."""
    global _enabled
    _enabled = True


def enabled():
    return _enabled


# ----------------------------------------------------------------- tables

_WRITE = re.compile(
    r"""^\s*(?:with\b.*?\)\s*)?(?:
        insert\s+(?:or\s+\w+\s+)?into\s+(?P<i>[\w."`\[\]]+)
      | replace\s+into\s+(?P<r>[\w."`\[\]]+)
      | update\s+(?:or\s+\w+\s+)?(?:only\s+)?(?P<u>[\w."`\[\]]+)
      | delete\s+from\s+(?:only\s+)?(?P<d>[\w."`\[\]]+)
      | truncate\s+(?:table\s+)?(?P<t>[\w."`\[\]]+)
      | (?:drop|alter)\s+table\s+(?:if\s+exists\s+)?(?P<a>[\w."`\[\]]+)
    )""", re.I | re.S | re.X)
_READ = re.compile(r"""\b(?:from|join)\s+(?!\()([\w."`\[\]]+)""", re.I)
_STRINGS = re.compile(r"'(?:[^']|'')*'")


def _table_name(raw):
    return raw.split(".")[-1].strip('"`[]').lower()


def written_table(sql):
    """The table a write statement changes, or None for reads."""
    m = _WRITE.match(_STRINGS.sub("''", sql or ""))
    if not m:
        return None
    return _table_name(next(v for v in m.groupdict().values() if v))


def read_tables(sql):
    """Tables a query reads (``FROM`` and ``JOIN``)."""
    return sorted({_table_name(t) for t in _READ.findall(_STRINGS.sub("''", sql or ""))})


def database_id(db):
    """A short id for ``db`` that every process connected to the same database agrees on."""
    ident = getattr(db, "live_identity", None)
    ident = ident() if callable(ident) else (ident or f"object:{id(db)}")
    key = hashlib.sha256(str(ident).encode()).hexdigest()[:12]
    _databases[key] = db
    return key


def table_changed(db, *tables):
    """Announce that ``tables`` of ``db`` changed (called by pyweb.db after commits)."""
    if not _enabled or not tables:
        return
    from .realtime import current_bus
    bus = current_bus()
    dbid = database_id(db)
    changed = sorted({t.lower() for t in tables if t})
    REGISTRY.invalidate(dbid, changed)        # a page rendered right after this write sees it
    for table in changed:
        bus.publish(f"{TABLE_CHANNEL}{dbid}:{table}", {"table": table})
    REGISTRY.wake()


# ---------------------------------------------------------------- queries

def _version(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]


class LiveRows(list):
    """The rows of a live query (a plain list of dicts) plus what the browser needs to follow it."""

    def __init__(self, rows, live):
        super().__init__(rows)
        self.live = live


class _Entry:
    def __init__(self, key, db, sql, params, tables, row_key=None):
        self.key, self.db, self.sql, self.params, self.tables = key, db, sql, params, tables
        self.row_key = row_key
        self.dbid = database_id(db)
        self.rows = None
        self.version = None
        self.dirty = True
        self.used = time.monotonic()
        self.watchers = 0

    def run(self):
        from .ssr import to_jsonable
        result = self.db.execute(self.sql, self.params)
        rows = to_jsonable(result.dicts() if hasattr(result, "dicts") else list(result))
        if len(rows) > MAX_ROWS:
            raise ValueError(f"live query returned {len(rows)} rows; live queries are for what a page shows "
                             f"(add a LIMIT, at most {MAX_ROWS})")
        if self.row_key is None and rows and all(isinstance(r, dict) and "id" in r for r in rows):
            self.row_key = "id"
        self.rows, self.version, self.dirty = rows, _version(rows), False
        return rows


class _Registry:
    """The live queries this process re-runs, and the thread that watches their tables."""

    def __init__(self):
        self.entries: dict[str, _Entry] = {}
        self.seen: dict[str, int] = {}          # table channel -> last bus id handled
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.thread = None
        self.inline = False                     # no threads (e.g. Pyodide): re-run right after each write

    def key(self, dbid, sql, params, tables):
        raw = json.dumps([dbid, sql, list(params), tables], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def add(self, db, sql, params, tables, row_key=None):
        from .realtime import current_bus
        dbid = database_id(db)
        key = self.key(dbid, sql, params, tables)
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                entry = self.entries[key] = _Entry(key, db, sql, params, tables, row_key)
                bus = current_bus()
                for table in tables:
                    chan = f"{TABLE_CHANNEL}{dbid}:{table}"
                    if chan not in self.seen:
                        self.seen[chan] = bus.position(chan)
                        bus.channel(chan).subscribe(lambda _msg: self.wake())
            entry.used = time.monotonic()
        self._start()
        return entry

    def wake(self):
        if self.inline:
            self.check()
        else:
            self.event.set()

    def invalidate(self, dbid, tables):
        with self.lock:
            for entry in self.entries.values():
                if entry.dbid == dbid and any(t in tables for t in entry.tables):
                    entry.dirty = True

    def watching(self, key, delta):
        with self.lock:
            entry = self.entries.get(key)
            if entry is not None:
                entry.watchers = max(0, entry.watchers + delta)
                entry.used = time.monotonic()

    def _start(self):
        with self.lock:
            if self.inline or (self.thread is not None and self.thread.is_alive()):
                return
            try:
                self.thread = threading.Thread(target=self._loop, name="pyweb-live", daemon=True)
                self.thread.start()
            except RuntimeError:
                self.inline = True

    def _loop(self):
        while True:
            self.event.wait(TICK_SECONDS)
            if self.event.is_set():
                time.sleep(DEBOUNCE_SECONDS)       # let a burst of writes settle into one re-run
                self.event.clear()
            try:
                self.check()
            except Exception:  # noqa: BLE001 - keep watching; a failing query is retried next change
                pass

    def check(self):
        """Re-run queries whose tables changed; publish rows that differ. Returns the keys re-run."""
        from .realtime import current_bus
        bus = current_bus()
        changed = set()
        with self.lock:
            for chan, last in list(self.seen.items()):
                news = bus.since(chan, last, limit=1000)
                if news:
                    self.seen[chan] = news[-1][0]
                    changed.add(chan[len(TABLE_CHANNEL):])
            now = time.monotonic()
            for key, entry in list(self.entries.items()):
                if not entry.watchers and now - entry.used > IDLE_SECONDS:
                    del self.entries[key]
            due = [e for e in self.entries.values()
                   if any(f"{e.dbid}:{t}" in changed for t in e.tables)]
        for entry in due:
            entry.dirty = True
            before, old_rows = entry.version, entry.rows
            try:
                rows = entry.run()
            except Exception:  # noqa: BLE001
                continue
            if entry.version != before:
                chan = LIVE_CHANNEL + entry.key
                latest = bus.since(chan, max(0, bus.position(chan) - 1), limit=1)
                if latest and isinstance(latest[-1][1], dict) and latest[-1][1].get("version") == entry.version:
                    continue                        # another process already sent these rows
                bus.publish(chan, change_message(old_rows, rows, entry.row_key, before, entry.version))
        return [e.key for e in due]


REGISTRY = _Registry()


# ---------------------------------------------------------------- patches

def diff(old, new, key):
    """Operations that turn ``old`` rows into ``new`` (both lists of dicts keyed by ``key``), or None.

    ``["d", k]`` deletes, ``["u", row]`` replaces the row with the same key,
    ``["i", index, row]`` inserts, ``["o", [keys]]`` reorders. None when the
    rows can't be keyed (missing or repeated keys).
    """
    if key is None or old is None:
        return None
    try:
        old_keys = [r[key] for r in old]
        new_keys = [r[key] for r in new]
    except (KeyError, TypeError):
        return None
    old_set, new_set = set(old_keys), set(new_keys)
    if len(old_set) != len(old_keys) or len(new_set) != len(new_keys):
        return None
    before = dict(zip(old_keys, old))
    ops = [["d", k] for k in old_keys if k not in new_set]
    ops += [["u", r] for k, r in zip(new_keys, new) if k in old_set and before[k] != r]
    kept_old = [k for k in old_keys if k in new_set]
    kept_new = [k for k in new_keys if k in old_set]
    if kept_old == kept_new:
        ops += [["i", i, r] for i, (k, r) in enumerate(zip(new_keys, new)) if k not in old_set]
    else:
        ops += [["i", 0, r] for k, r in zip(new_keys, new) if k not in old_set]
        ops.append(["o", new_keys])
    return ops


def apply(rows, ops, key):
    """``rows`` after ``ops`` (what the browser does; unchanged rows stay the same objects)."""
    out = list(rows)
    for op in ops:
        kind = op[0]
        if kind == "d":
            out = [r for r in out if r[key] != op[1]]
        elif kind == "u":
            out = [op[1] if r[key] == op[1][key] else r for r in out]
        elif kind == "i":
            out.insert(op[1], op[2])
        elif kind == "o":
            by_key = {r[key]: r for r in out}
            out = [by_key[k] for k in op[1]]
    return out


def change_message(old_rows, rows, row_key, prev, version):
    """What to publish when a query's rows change: a patch when it's smaller than the rows."""
    full = {"version": version, "rows": rows}
    ops = diff(old_rows, rows, row_key) if prev is not None else None
    if ops is None:
        return full
    patch = {"version": version, "prev": prev, "key": row_key, "ops": ops}
    size = len(json.dumps(patch, default=str))
    return patch if size < len(json.dumps(full, default=str)) else full


def snapshot(spec, secret):
    """``{"version", "rows"}`` now, for a browser that missed a change; None if the spec is bad."""
    key = adopt_spec(spec, secret)
    if key is None:
        return None
    entry = REGISTRY.entries.get(key)
    if entry is None:
        return None
    if entry.dirty or entry.rows is None:
        entry.run()
    entry.used = time.monotonic()
    return {"version": entry.version, "rows": entry.rows, "key": entry.row_key}


# ------------------------------------------------------------------ specs

def _sign(secret, payload):
    import hmac
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, b"pyweb-live:" + payload.encode(), hashlib.sha256).hexdigest()[:40]


def make_spec(secret, dbid, sql, params, tables, row_key=None):
    from .realtime import _b64
    payload = _b64(json.dumps([dbid, sql, list(params), tables, row_key], default=str).encode())
    return f"{payload}.{_sign(secret, payload)}"


def adopt_spec(token, secret):
    """Register the query a signed spec describes (a page rendered elsewhere is listening). Returns its key."""
    import hmac
    from .keys import any_valid
    from .realtime import _unb64
    payload, _, sig = (token or "").partition(".")
    candidates = secret if isinstance(secret, (list, tuple)) else [secret]
    if not payload or not any_valid(candidates, lambda k: hmac.compare_digest(sig, _sign(k, payload))):
        return None
    try:
        dbid, sql, params, tables, *rest = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    db = _databases.get(dbid)
    if db is None:
        return None
    return REGISTRY.add(db, sql, tuple(params), tables, rest[0] if rest else None).key


# -------------------------------------------------------------------- API

def live(db, sql, params=(), *, tables=None, key=None):
    """Run ``sql`` now and keep the page variable it's assigned to up to date in the browser.

    ``tables`` lists the tables to watch; by default the ones after
    ``FROM``/``JOIN`` in the query. ``key`` is the column that identifies a
    row (``id`` is used when every row has one), so changes travel as small
    patches. Call it in a page or layout body.
    """
    from .context import sign_key
    from .realtime import current_bus, current_sid, make_feed
    params = tuple(params)
    watch = sorted({t.lower() for t in tables}) if tables else read_tables(sql)
    if not watch:
        raise ValueError("live() couldn't find a table in the query; pass tables=[...]")
    enable()
    entry = REGISTRY.add(db, sql, params, watch, key)
    from .realtime import Bus
    shared = type(current_bus()) is Bus and not entry.dirty and entry.rows is not None
    rows = entry.rows if shared else entry.run()   # one process: viewers share the last run
    chan = LIVE_CHANNEL + entry.key
    meta = {"feed": make_feed(chan, sign_key("feed"), since=current_bus().position(chan), sid=current_sid()),
            "spec": make_spec(sign_key("live"), entry.dbid, sql, params, watch, entry.row_key),
            "version": entry.version}
    return LiveRows(rows, meta)


class WatchedStream:
    """An event stream for a live query: while it's open the query counts as watched (never forgotten)."""

    def __init__(self, stream, key):
        self.stream, self.key = stream, key

    def __iter__(self):
        REGISTRY.watching(self.key, 1)
        try:
            yield from self.stream
        finally:
            REGISTRY.watching(self.key, -1)

    async def aiter(self):
        REGISTRY.watching(self.key, 1)
        try:
            async for chunk in self.stream.aiter():
                yield chunk
        finally:
            REGISTRY.watching(self.key, -1)

    def snapshot(self):
        return self.stream.snapshot()

    def close(self):
        self.stream.close()
