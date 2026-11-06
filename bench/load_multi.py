#!/usr/bin/env python3
"""Multi-turn load test: threads run in parallel, turns inside one thread are serial.
Usage: load_multi.py -c 10 --threads 100 --turns 8 [--note TEXT]. Token comes from DF_PAT_FILE."""
import argparse, asyncio, json, os, re, subprocess, time
import httpx

BASE = os.environ.get("DF_BASE", "http://127.0.0.1:8001")


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    k = (len(v) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f), 3)


async def one_turn(client, tok, thread_id, text):
    cfg = {"model_name": "mock-llm", "thinking_enabled": False, "is_plan_mode": False}
    if thread_id:
        cfg["thread_id"] = thread_id
    body = {"input": {"messages": [{"role": "user", "content": text}]},
            "config": {"recursion_limit": 100, "configurable": cfg},
            "stream_mode": ["values", "messages-tuple", "custom"]}
    headers = {"Authorization": "Bearer " + tok, "Accept": "text/event-stream"}
    t0 = time.perf_counter()
    status, ended, err, new_tid = None, False, None, thread_id
    try:
        async with client.stream("POST", BASE + "/api/runs/stream", json=body, headers=headers) as r:
            status = r.status_code
            loc = r.headers.get("content-location", "")
            m = re.search(r"/threads/([^/]+)/runs/", loc)
            if m:
                new_tid = m.group(1)
            async for line in r.aiter_lines():
                if line.startswith("event:") and line[6:].strip() == "end":
                    ended = True
    except Exception as e:
        err = type(e).__name__
    return {"status": status, "ended": ended, "err": err, "total": time.perf_counter() - t0}, new_tid


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", type=int, default=10)
    ap.add_argument("--threads", type=int, default=100)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    tok = open(os.path.expanduser(os.environ.get("DF_PAT_FILE", "~/.deerflow_pat"))).read().strip()
    try:
        httpx.get(BASE + "/api/v1/auth/setup-status", timeout=5)
    except Exception as e:
        raise SystemExit("预检失败:连不上 %s (%s)" % (BASE, type(e).__name__))

    todo = iter(range(a.threads))
    results = []  # (turn_index, result)
    done = {"n": 0}

    async def worker():
        while True:
            try:
                next(todo)
            except StopIteration:
                return
            tid = None
            for t in range(a.turns):
                res, tid = await one_turn(client, tok, tid, "turn %d: please continue." % (t + 1))
                results.append((t + 1, res))
                if not (res["status"] == 200 and res["ended"]) or not tid:
                    break  # stop this thread on failure
            done["n"] += 1
            if done["n"] % 10 == 0:
                print("  已完成对话 %d/%d" % (done["n"], a.threads), flush=True)

    limits = httpx.Limits(max_connections=a.c + 5, max_keepalive_connections=a.c + 5)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0), limits=limits) as client:
        for _ in range(2):  # warm-up: two short 2-turn threads, not counted
            tid = None
            for t in range(2):
                _, tid = await one_turn(client, tok, tid, "warm %d" % t)
        t0 = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(a.c)])
        wall = time.perf_counter() - t0

    ok = [(t, r) for t, r in results if r["status"] == 200 and r["ended"]]
    bad = {}
    for t, r in results:
        if not (r["status"] == 200 and r["ended"]):
            k = str(r["status"] or r["err"] or "no_end")
            bad[k] = bad.get(k, 0) + 1
    by_turn = {}
    for t, r in ok:
        by_turn.setdefault(t, []).append(r["total"])
    out = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "c": a.c, "threads": a.threads, "turns": a.turns, "note": a.note,
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "ok_runs": len(ok), "expected_runs": a.threads * a.turns, "bad": bad,
        "wall_s": round(wall, 2), "throughput_rps": round(len(ok) / wall, 2),
        "total_p50": pct([r["total"] for _, r in ok], 50), "total_p95": pct([r["total"] for _, r in ok], 95),
        "p50_by_turn": {str(t): pct(v, 50) for t, v in sorted(by_turn.items())},
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + "_multi_t%d.json" % a.turns), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


asyncio.run(main())
