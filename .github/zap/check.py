"""Fail the ZAP job on any high-risk alert (the rules file decides the rest)."""
import json
import sys

report = json.load(open(sys.argv[1]))
high = [(a["pluginid"], a["name"]) for site in report.get("site", []) for a in site.get("alerts", [])
        if int(a.get("riskcode", 0)) >= 3]
for pid, name in high:
    print(f"high risk: [{pid}] {name}")
sys.exit(1 if high else 0)
