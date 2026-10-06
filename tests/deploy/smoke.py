"""Smoke-test a deployed stack (run by CI after `pyweb deploy compose --replicas 2` + `docker compose up`).

Checks, through the front proxy: requests are spread over the web replicas, background jobs run in
the worker container, and one browser socket sees changes made anywhere (live updates via Redis).
"""

import collections
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wsclient import Client  # noqa: E402

BASE = os.environ.get("BASE", "http://127.0.0.1:80")


def rpc(name, **args):
    req = urllib.request.Request(BASE + f"/__pyweb/rpc/{name}", data=json.dumps({"args": args}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": BASE, "Connection": "close"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]


def main():
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/readyz", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    html = urllib.request.urlopen(BASE + "/").read().decode()
    feed, spec = re.search(r'"feed":\s*"([^"]+)".*?"spec":\s*"([^"]+)"', html, re.S).groups()

    class Target:
        port = int(BASE.rsplit(":", 1)[1])
        url = BASE
    sock = Client(Target, origin=BASE)
    sock.next("hello")
    sock.send({"t": "sub", "s": 1, "feed": feed, "live": spec})
    time.sleep(0.5)

    webs, ids = collections.Counter(), []
    for i in range(12):
        out = rpc("place", item=f"book {i}")
        webs[out["web"]] += 1
        ids.append(out["id"])
    print("answered by:", dict(webs))
    assert len(webs) == 2, "both web replicas should answer"

    deadline = time.time() + 30
    while True:
        states = [rpc("status", order_id=i) for i in ids]
        if all(s["status"] == "shipped" for s in states) or time.time() > deadline:
            break
        time.sleep(1)
    shippers = {s["by"] for s in states}
    print("shipped by:", shippers)
    assert all(s["status"] == "shipped" for s in states), states
    assert not shippers & set(webs), "jobs should run in the worker, not the web containers"

    seen = []
    while True:
        try:
            seen.append(sock.next("m", timeout=3)["d"])
        except AssertionError:
            break
    print("live messages:", len(seen))
    assert seen and "shipped" in json.dumps(seen[-1]), "the socket should see the final state"
    print("deploy smoke test: ok")


if __name__ == "__main__":
    main()
