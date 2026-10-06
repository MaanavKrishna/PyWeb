"""Metrics in Prometheus' text format, with no dependency.

Counters, gauges and histograms, labelled. With ``pyweb serve --workers N``
each process writes a snapshot to a shared folder every few seconds, and
``/metrics`` (answered by whichever process gets the scrape) adds them up,
so a scrape always describes the whole server, not one random process.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import threading
import time

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
MAX_SERIES = 2000            # per metric: a label with unbounded values must not exhaust memory


class _Metric:
    kind = "untyped"

    def __init__(self, name, help_, labels=()):
        self.name, self.help, self.label_names = name, help_, tuple(labels)
        self.lock = threading.Lock()
        self.series: dict = {}

    def _labels(self, labels):
        # Hot path (every request): one tuple in a fixed order, no sorting.
        names = self.label_names
        if len(labels) != len(names):
            raise ValueError(f"{self.name} takes labels {names}, got {tuple(labels)}")
        try:
            key = tuple((n, str(labels[n])) for n in names) if names else ()
        except KeyError:
            raise ValueError(f"{self.name} takes labels {names}, got {tuple(labels)}") from None
        if key not in self.series and len(self.series) >= MAX_SERIES:
            key = tuple((n, "other") for n in names)
        return key


class Counter(_Metric):
    kind = "counter"

    def inc(self, amount=1.0, **labels):
        key = self._labels(labels)
        with self.lock:
            self.series[key] = self.series.get(key, 0.0) + amount

    def value(self, **labels):
        return self.series.get(tuple((n, str(labels[n])) for n in self.label_names), 0.0)


class Gauge(_Metric):
    """A value that goes up and down; or ``Gauge(..., callback=fn)`` read at scrape time.

    ``merge`` says how processes combine: ``sum`` (open sockets), ``max`` (a value every
    process reads from the same place, like jobs waiting) or ``min`` (the oldest start time).
    """

    kind = "gauge"

    def __init__(self, name, help_, labels=(), callback=None, merge="sum"):
        super().__init__(name, help_, labels)
        self.callback = callback
        self.merge = merge

    def set(self, value, **labels):
        key = self._labels(labels)
        with self.lock:
            self.series[key] = float(value)

    def inc(self, amount=1.0, **labels):
        key = self._labels(labels)
        with self.lock:
            self.series[key] = self.series.get(key, 0.0) + amount

    def dec(self, amount=1.0, **labels):
        self.inc(-amount, **labels)

    def collect(self):
        if self.callback is not None:
            try:
                for labels, value in self.callback():
                    self.set(value, **labels)
            except Exception:  # noqa: BLE001 - a broken callback must not break the scrape
                pass
        return self.series


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name, help_, labels=(), buckets=DEFAULT_BUCKETS):
        super().__init__(name, help_, labels)
        self.buckets = tuple(sorted(buckets))

    def observe(self, value, **labels):
        key = self._labels(labels)
        with self.lock:
            s = self.series.get(key)
            if s is None:
                s = self.series[key] = {"counts": [0] * len(self.buckets), "sum": 0.0, "count": 0}
            i = bisect.bisect_left(self.buckets, value)
            if i < len(self.buckets):
                s["counts"][i] += 1
            s["sum"] += value
            s["count"] += 1


class Registry:
    def __init__(self):
        self.metrics: dict[str, _Metric] = {}
        self.lock = threading.Lock()

    def _get(self, cls, name, help_, labels=(), **kw):
        with self.lock:
            m = self.metrics.get(name)
            if m is None:
                m = self.metrics[name] = cls(name, help_, labels, **kw)
            return m

    def counter(self, name, help_, labels=()):
        return self._get(Counter, name, help_, labels)

    def gauge(self, name, help_, labels=(), callback=None, merge="sum"):
        g = self._get(Gauge, name, help_, labels, merge=merge)
        if callback is not None:
            g.callback = callback
        return g

    def histogram(self, name, help_, labels=(), buckets=DEFAULT_BUCKETS):
        return self._get(Histogram, name, help_, labels, buckets=buckets)

    # ------------------------------------------------------------ snapshots
    def snapshot(self):
        """Everything as JSON-friendly data (for merging across processes)."""
        out = {}
        for name, m in list(self.metrics.items()):
            series = m.collect() if isinstance(m, Gauge) else m.series
            with m.lock:
                out[name] = {"kind": m.kind, "help": m.help, "labels": list(m.label_names),
                             "buckets": list(getattr(m, "buckets", ())), "merge": getattr(m, "merge", "sum"),
                             "series": [[list(map(list, k)), v] for k, v in series.items()]}
        return out


REGISTRY = Registry()


def merge(snapshots):
    """Add up snapshots from several processes (gauges are summed too: open sockets, running jobs)."""
    merged = {}
    for snap in snapshots:
        for name, m in snap.items():
            target = merged.setdefault(name, {**m, "series": {}})
            for labels, value in m["series"]:
                key = tuple(tuple(x) for x in labels)
                if m["kind"] == "histogram":
                    cur = target["series"].get(key)
                    if cur is None:
                        target["series"][key] = {"counts": list(value["counts"]), "sum": value["sum"],
                                                 "count": value["count"]}
                    else:
                        cur["counts"] = [a + b for a, b in zip(cur["counts"], value["counts"])]
                        cur["sum"] += value["sum"]
                        cur["count"] += value["count"]
                elif key in target["series"] and m.get("merge") in ("max", "min"):
                    pick = max if m["merge"] == "max" else min
                    target["series"][key] = pick(target["series"][key], value)
                else:
                    target["series"][key] = target["series"].get(key, 0.0) + value
    return merged


def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _lbl(pairs, extra=()):
    items = [f'{k}="{_esc(v)}"' for k, v in (*pairs, *extra)]
    return "{" + ",".join(items) + "}" if items else ""


def _num(v):
    v = float(v)
    if v == math.inf:
        return "+Inf"
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def render(merged):
    """Prometheus text exposition format (version 0.0.4)."""
    lines = []
    for name in sorted(merged):
        m = merged[name]
        lines.append(f"# HELP {name} {m['help']}")
        lines.append(f"# TYPE {name} {m['kind']}")
        series = m["series"]
        items = series.items() if isinstance(series, dict) else [(tuple(tuple(x) for x in k), v) for k, v in series]
        for key, value in sorted(items, key=lambda kv: kv[0]):
            if m["kind"] == "histogram":
                running = 0
                for upper, count in zip(m["buckets"], value["counts"]):
                    running += count
                    lines.append(f"{name}_bucket{_lbl(key, [('le', _num(upper))])} {running}")
                lines.append(f"{name}_bucket{_lbl(key, [('le', '+Inf')])} {value['count']}")
                lines.append(f"{name}_sum{_lbl(key)} {_num(round(value['sum'], 6))}")
                lines.append(f"{name}_count{_lbl(key)} {value['count']}")
            else:
                lines.append(f"{name}{_lbl(key)} {_num(value)}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- several processes

def shared_dir():
    return os.environ.get("PYWEB_METRICS_DIR") or None


def dump(path=None):
    """Write this process's snapshot to the shared folder (atomically)."""
    folder = path or shared_dir()
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.getpid()}.tmp")
    with open(tmp, "w") as fh:
        json.dump({"pid": os.getpid(), "at": time.time(), "metrics": REGISTRY.snapshot()}, fh)
    os.replace(tmp, os.path.join(folder, f"{os.getpid()}.json"))


def collect_all():
    """Every process's snapshot (this one's live), merged."""
    snaps = [REGISTRY.snapshot()]
    folder = shared_dir()
    if folder and os.path.isdir(folder):
        for fn in os.listdir(folder):
            if not fn.endswith(".json") or fn == f"{os.getpid()}.json":
                continue
            try:
                with open(os.path.join(folder, fn)) as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            snap = data.get("metrics", {})
            if not _alive(data.get("pid")):
                # A process that's gone: its counters still count (they're totals), its gauges don't.
                snap = {k: v for k, v in snap.items() if v.get("kind") != "gauge"}
            snaps.append(snap)
    return merge(snaps)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


_dumper = None


def start_dumping(every=None):
    """In a worker process: write snapshots regularly (and at exit) when a shared folder is set."""
    global _dumper
    if not shared_dir() or _dumper is not None:
        return
    every = float(every or os.environ.get("PYWEB_METRICS_EVERY") or 5.0)
    import atexit

    def loop():
        while True:
            time.sleep(every)
            try:
                dump()
            except OSError:
                pass

    _dumper = threading.Thread(target=loop, name="pyweb-metrics", daemon=True)
    _dumper.start()
    atexit.register(lambda: dump() if shared_dir() else None)
