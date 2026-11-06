#!/usr/bin/env python3
"""One streaming run via PAT. Never prints the token."""
import json, os, time, urllib.error, urllib.request

BASE = os.environ.get("DF_BASE", "http://127.0.0.1:8001")
tok = open(os.path.expanduser("~/.deerflow_pat")).read().strip()
body = {
    "input": {"messages": [{"role": "user", "content": "hi"}]},
    "config": {"recursion_limit": 100,
               "configurable": {"model_name": "mock-llm", "thinking_enabled": False, "is_plan_mode": False}},
    "stream_mode": ["values", "messages-tuple", "custom"],
}
req = urllib.request.Request(
    BASE + "/api/runs/stream", data=json.dumps(body).encode(), method="POST",
    headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
             "Accept": "text/event-stream"})
t0 = time.time()
first = None
events = {}
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print("状态码:", r.status)
        print("Content-Location:", r.headers.get("Content-Location"))
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
                events[ev] = events.get(ev, 0) + 1
                if first is None:
                    first = time.time() - t0
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", "replace")[:300])
print("首个事件耗时: %s" % ("%.2fs" % first if first else "无"))
print("事件统计:", events)
print("总耗时: %.2fs" % (time.time() - t0))
