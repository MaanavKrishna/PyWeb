"""Every test THREAT_MODEL.md cites as proof exists (so the document can't quietly go stale)."""

import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_every_cited_test_exists():
    text = open(os.path.join(ROOT, "THREAT_MODEL.md"), encoding="utf-8").read()
    cited = re.findall(r"`((?:e2e/|integration/)?test_\w+\.py)(?:::(test_\w+))?`", text)
    assert len(cited) > 40
    missing = []
    for path, name in cited:
        full = os.path.join(ROOT, "tests", path)
        if not os.path.isfile(full):
            missing.append(path)
        elif name and not re.search(rf"^def {name}\(", open(full, encoding="utf-8").read(), re.M):
            missing.append(f"{path}::{name}")
    assert missing == []
