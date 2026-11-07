#!/usr/bin/env python3
"""Snapshot: periodic-reconciliation recoveries per Gateway (from logs) and runs.status counts (Postgres)."""
import json, os, re, subprocess
LOGS = os.path.expanduser("~/work/deer-flow/.deer-flow/multi-instance/logs")


def recovered(path):
    n = 0
    try:
        for line in open(path, errors="replace"):
            m = re.search(r"Periodic reconciliation recovered (\d+) orphaned run", line)
            if m:
                n += int(m.group(1))
    except FileNotFoundError:
        pass
    return n


def db():
    p = subprocess.run(["docker", "exec", "deerflow-mi-postgres", "psql", "-U", "deerflow", "-d", "deerflow", "-At",
                        "-F", "|", "-c", "select status, count(*) from runs group by status order by 1"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("psql 失败: " + p.stderr.strip()[:200])
    d = {}
    for line in p.stdout.strip().splitlines():
        if "|" in line:
            k, v = line.split("|")
            d[k] = int(v)
    return d


print(json.dumps({"recovered_a": recovered(LOGS + "/gateway-a.log"),
                  "recovered_b": recovered(LOGS + "/gateway-b.log"), "runs": db()}))
