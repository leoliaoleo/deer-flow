#!/usr/bin/env python3
"""Closed-loop load test for POST /api/runs/stream (PAT auth). Never prints the token."""
import argparse, asyncio, json, os, subprocess, time
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


def gw_rss_mb():
    try:
        pids = subprocess.run(["pgrep", "-f", "uvicorn app.gateway.app:app"],
                              capture_output=True, text=True).stdout.split()
        best = 0
        for pid in pids:
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("VmRSS:"):
                    best = max(best, int(line.split()[1]) // 1024)
        return best
    except Exception:
        return None


async def one(client, tok, model):
    body = {
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "config": {"recursion_limit": 100,
                   "configurable": {"model_name": model, "thinking_enabled": False, "is_plan_mode": False}},
        "stream_mode": ["values", "messages-tuple", "custom"],
    }
    headers = {"Authorization": "Bearer " + tok, "Accept": "text/event-stream"}
    t0 = time.perf_counter()
    ttfe, ttft, status, ended, err = None, None, None, False, None
    try:
        async with client.stream("POST", BASE + "/api/runs/stream", json=body, headers=headers) as r:
            status = r.status_code
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    if ttfe is None:
                        ttfe = time.perf_counter() - t0
                    ev = line[6:].strip()
                    if ev == "messages" and ttft is None:
                        ttft = time.perf_counter() - t0
                    if ev == "end":
                        ended = True
    except Exception as e:
        err = type(e).__name__
    return {"status": status, "ttfe": ttfe, "ttft": ttft, "total": time.perf_counter() - t0, "ended": ended, "err": err}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", type=int, default=1, help="concurrency")
    ap.add_argument("-n", type=int, default=10, help="total runs")
    ap.add_argument("--model", default="mock-llm")
    ap.add_argument("--note", default="")
    ap.add_argument("--warmup", type=int, default=3)
    a = ap.parse_args()
    tok = open(os.path.expanduser("~/.deerflow_pat")).read().strip()
    todo = iter(range(a.n))
    res = []

    async def worker():
        while True:
            try:
                next(todo)
            except StopIteration:
                return
            res.append(await one(client, tok, a.model))
            if len(res) % 25 == 0:  # progress
                print("  已完成 %d/%d" % (len(res), a.n), flush=True)

    # preflight: refuse to record numbers when the gateway is unreachable
    try:
        httpx.get(BASE + "/api/v1/auth/setup-status", timeout=5)
    except Exception as e:
        raise SystemExit("预检失败:连不上 %s (%s),结果不保存。" % (BASE, type(e).__name__))
    rss0 = gw_rss_mb()
    limits = httpx.Limits(max_connections=a.c + 5, max_keepalive_connections=a.c + 5)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0), limits=limits) as client:
        for _ in range(a.warmup):
            await one(client, tok, a.model)
        t0 = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(a.c)])
        wall = time.perf_counter() - t0
    rss1 = gw_rss_mb()

    ok = [r for r in res if r["status"] == 200 and r["ended"]]
    bad = {}
    for r in res:
        if not (r["status"] == 200 and r["ended"]):
            key = str(r["status"] or r["err"] or "no_end")
            bad[key] = bad.get(key, 0) + 1
    out = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "c": a.c, "n": a.n, "note": a.note,
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "ok": len(ok), "bad": bad, "wall_s": round(wall, 2), "throughput_rps": round(len(ok) / wall, 2),
        "ttfe_p50": pct([r["ttfe"] for r in ok if r["ttfe"]], 50), "ttfe_p95": pct([r["ttfe"] for r in ok if r["ttfe"]], 95),
        "ttft_p50": pct([r["ttft"] for r in ok if r["ttft"]], 50), "ttft_p95": pct([r["ttft"] for r in ok if r["ttft"]], 95), "total_p50": pct([r["total"] for r in ok], 50), "total_p95": pct([r["total"] for r in ok], 95),
        "total_p99": pct([r["total"] for r in ok], 99), "gateway_rss_mb": [rss0, rss1],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + f"_c{a.c}.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


asyncio.run(main())
