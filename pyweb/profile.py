"""``pyweb profile``: what your app spends its time on, while you use it.

On Python 3.15+ the app runs under Tachyon, CPython's sampling profiler
(``profiling.sampling``), and you get an interactive flame graph. Sampling
reads the process from outside, so the app runs at full speed.

Before 3.15 a small sampler inside the process looks at every thread a
thousand times a second and writes the busiest functions (where time was
spent, and what it was spent under) to a text report.
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
import time


def tachyon_available():
    if sys.version_info < (3, 15):
        return False
    import importlib.util
    try:
        return importlib.util.find_spec("profiling.sampling") is not None
    except ModuleNotFoundError:
        return False


def run(app, *, seconds=15.0, out=None, port=8000, mode="cpu", printer=print):
    """Serve ``app`` on ``port`` for ``seconds`` while sampling it; returns the report's path."""
    app = os.path.abspath(app)
    if tachyon_available():
        out = os.path.abspath(out or "profile.html")
        printer(f"profiling http://127.0.0.1:{port} for {seconds:g}s (Tachyon, {mode} time): use the app now")
        cmd = [sys.executable, "-m", "profiling.sampling", "run", "-a", "--flamegraph", "-o", out,
               "-d", str(max(1, round(seconds))), "--mode", mode, "-m", "pyweb.cli", "serve", app, "--port", str(port)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"the profiler failed: {(done.stderr or done.stdout)[-800:]}")
        _fix_embedded_script_ends(out)
        printer(f"flame graph: {out} (open it in a browser)")
        return out
    out = os.path.abspath(out or "profile.txt")
    printer(f"profiling http://127.0.0.1:{port} for {seconds:g}s (built-in sampler; Python 3.15 draws a "
            "flame graph): use the app now")
    report = _sample_in_process(app, seconds, port)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report)
    printer(report.split("\n\n", 1)[0])
    printer(f"report: {out}")
    return out


def _fix_embedded_script_ends(path):
    """Tachyon (3.15.1) embeds source lines in its report without escaping ``</script``; a line
    that contains one (PyWeb renders HTML, so its source has several) ends the report's script
    early and the flame graph stays empty. Escape every ``</script`` that isn't a real tag."""
    import re
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    fixed = re.sub(r"</script(?!>\s*<)", r"<\\/script", html)
    if fixed != html:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fixed)


_IDLE = ("threading.py:wait", "queue.py:get", "selectors.py:select", "thread.py:_worker",
         "threading.py:_wait_for_tstate_lock", "base_events.py:_run_once", "socket.py:accept")


def _free_threaded():
    return not getattr(sys, "_is_gil_enabled", lambda: True)()


def _sample_in_process(app, seconds, port, rate=1000):
    if _free_threaded():
        # Reading another thread's frames while it runs is only safe when the GIL stops it.
        raise RuntimeError("the built-in sampler needs the GIL: run it with PYTHON_GIL=1 "
                           "(or use Python 3.15t, which profiles with Tachyon)")
    from pyweb.hosting import Site
    from pyweb.net.server import Running
    running = Running(Site(app), host="127.0.0.1", port=port)
    me = threading.get_ident()
    own = collections.Counter()      # samples where the function itself was running
    under = collections.Counter()    # samples where it was anywhere on the stack
    samples = 0
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            for tid, frame in sys._current_frames().items():
                if tid == me:
                    continue
                name = _name(frame)
                if name.startswith(_IDLE):
                    continue
                samples += 1
                own[name] += 1
                seen = set()
                while frame is not None:
                    n = _name(frame)
                    if n not in seen:
                        under[n] += 1
                        seen.add(n)
                    frame = frame.f_back
            time.sleep(1 / rate)
    finally:
        running.stop(timeout=2.0)
    if not samples:
        return "no samples: the app was idle (open it in a browser while profiling)\n"
    lines = [f"{samples} samples in {seconds:g}s", ""]
    lines.append("time spent in (self):")
    lines += [f"  {c * 100 / samples:5.1f}%  {n}" for n, c in own.most_common(25)]
    lines += ["", "time spent under (inclusive):"]
    lines += [f"  {c * 100 / samples:5.1f}%  {n}" for n, c in under.most_common(25)]
    return "\n".join(lines) + "\n"


def _name(frame):
    code = frame.f_code
    return f"{os.path.basename(code.co_filename)}:{code.co_name}"
