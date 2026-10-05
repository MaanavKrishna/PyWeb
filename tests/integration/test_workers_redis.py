"""`pyweb serve --workers 2` with Redis: a socket on one worker hears publishes made on
either, and SIGTERM drains both. Needs PYWEB_TEST_REDIS."""

import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

REDIS = os.environ.get("PYWEB_TEST_REDIS")
pytestmark = pytest.mark.skipif(not REDIS or not hasattr(os, "fork"), reason="set PYWEB_TEST_REDIS")

APP = '''
import os
from pyweb import App, server, channel, publish

app = App()

@server
def shout(text: str) -> int:
    publish("room", {"text": text, "pid": os.getpid()})
    return os.getpid()

@app.page("/")
def Home():
    feed = channel("room")
    <p id="feed">{feed}</p>
'''


def test_two_workers_share_live_updates(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from test_net import Client

    (tmp_path / "app.pyweb").write_text(APP)
    subprocess.run([sys.executable, "-m", "pyweb.cli", "build", str(tmp_path / "app.pyweb"),
                    "--out", str(tmp_path / "dist")], check=True, capture_output=True)
    env = dict(os.environ, PYWEB_REDIS_URL=REDIS, PYWEB_AUTH_SECRET="s" * 40, PYWEB_ENV="development")
    with __import__("socket").socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "serve", str(tmp_path / "dist"), "--host",
                             "127.0.0.1", "--port", str(port), "--workers", "2"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        feed = re.search(r'id="feed">([^<]+)<', urllib.request.urlopen(url).read().decode()).group(1)

        class R:
            pass
        r = R()
        r.port, r.url = port, url
        c = Client(r)
        c.send({"t": "sub", "s": 1, "feed": feed})
        time.sleep(0.5)
        pids = set()
        for i in range(12):                                   # new connections: spread over both workers
            req = urllib.request.Request(url + "/__pyweb/rpc/shout", data=f'{{"args": {{"text": "m{i}"}}}}'.encode(),
                                         headers={"Content-Type": "application/json", "Connection": "close"})
            pids.add(int(urllib.request.urlopen(req).read().decode().split(":")[1].strip(" }")))
        got = []
        while len(got) < 12:
            got.append(c.next("m", timeout=5)["d"]["text"])
        assert got == [f"m{i}" for i in range(12)]
        assert len(pids) == 2, "both workers should have answered"
    finally:
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=20)[0].decode()
    assert proc.returncode == 0, out
