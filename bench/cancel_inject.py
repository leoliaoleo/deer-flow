#!/usr/bin/env python3
"""Cancel B's in-flight runs from A; measure time to terminal status. Optional: freeze B (SIGSTOP) first."""
import argparse, asyncio, json, os, random, re, signal, subprocess, time
import httpx

A = os.environ.get("DF_A", "http://127.0.0.1:8001")
B = os.environ.get("DF_B", "http://127.0.0.1:8011")
REPO = os.path.expanduser("~/work/deer-flow")
TOK = open(os.path.expanduser(os.environ["DF_PAT_FILE"])).read().strip()
HDR = {"Authorization": "Bearer " + TOK}
TERMINAL = {"success", "error", "interrupted", "timeout", "cancelled", "canceled", "failed"}


def pid_of(port):
    p = subprocess.run(["ss", "-ltnp", "sport = :%d" % port], capture_output=True, text=True)
    m = re.search(r"pid=(\d+)", p.stdout)
    return int(m.group(1)) if m else None


def count(vals):
    d = {}
    for v in vals:
        d[str(v)] = d.get(str(v), 0) + 1
    return d


async def drive(client, r):
    body = {"input": {"messages": [{"role": "user", "content": "please answer slowly"}]},
            "config": {"recursion_limit": 100,
                       "configurable": {"model_name": "mock-llm", "thinking_enabled": False, "is_plan_mode": False}},
            "stream_mode": ["values", "messages-tuple", "custom"]}
    try:
        async with client.stream("POST", B + "/api/runs/stream", json=body,
                                 headers={**HDR, "Accept": "text/event-stream"}) as resp:
            m = re.search(r"/threads/([^/]+)/runs/([^/\s]+)", resp.headers.get("content-location", ""))
            if m:
                r["thread_id"], r["run_id"] = m.group(1), m.group(2)
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                    if ev == "messages":
                        r["ready"].set()
                    if ev == "end":
                        r["ended"] = True
    except Exception as e:
        r["broke"] = type(e).__name__
    finally:
        r["ready"].set()


async def status(client, r):
    try:
        resp = await client.get("%s/api/threads/%s/runs/%s" % (A, r["thread_id"], r["run_id"]), headers=HDR, timeout=10)
        if resp.status_code != 200:
            return "http%d" % resp.status_code, None
        d = resp.json()
        return d.get("status"), d.get("stop_reason")
    except Exception as e:
        return "exc:" + type(e).__name__, None


async def cancel(client, r, t0):
    try:
        resp = await client.post("%s/api/threads/%s/runs/%s/cancel?action=interrupt" % (A, r["thread_id"], r["run_id"]),
                                 headers=HDR, timeout=15)
        r["cancel_http"] = resp.status_code
    except Exception as e:
        r["cancel_http"] = "exc:" + type(e).__name__


async def watch(client, r, t0, limit):
    while time.time() - t0 < limit:
        st, sr = await status(client, r)
        if st in TERMINAL:
            r["term_s"], r["final"], r["stop_reason"] = round(time.time() - t0, 2), st, sr
            return
        await asyncio.sleep(0.25)
    r["final"] = "timeout"


async def trial(client, freeze, hold, limit, n, cancel_after=0.0):
    rs = [{"ready": asyncio.Event(), "thread_id": None, "run_id": None, "ended": False, "broke": None} for _ in range(n)]
    tasks = [asyncio.create_task(drive(client, r)) for r in rs]
    try:
        await asyncio.wait_for(asyncio.gather(*[r["ready"].wait() for r in rs]), 20)
    except asyncio.TimeoutError:
        print("  警告: 20 秒内并非所有运行都开始输出", flush=True)
    await asyncio.sleep(1.0)
    runs = [r for r in rs if r["run_id"]]
    bpid = pid_of(8011)
    frozen_at = None
    if freeze:
        os.kill(bpid, signal.SIGSTOP)
        frozen_at = time.time()
        await asyncio.sleep(0.2 + cancel_after)
    t0 = time.time()
    try:
        await asyncio.gather(*[cancel(client, r, t0) for r in runs])
        watchers = [asyncio.create_task(watch(client, r, t0, limit)) for r in runs]
        if freeze:
            await asyncio.sleep(max(0.0, hold + cancel_after - (time.time() - frozen_at)))
            os.kill(bpid, signal.SIGCONT)
            thawed_s = round(time.time() - t0, 2)
        else:
            thawed_s = None
        await asyncio.gather(*watchers)
    finally:
        if freeze:
            try:
                os.kill(bpid, signal.SIGCONT)
            except ProcessLookupError:
                pass
    await asyncio.wait(tasks, timeout=15)
    for t in tasks:
        t.cancel()
    ts = sorted(r["term_s"] for r in runs if "term_s" in r)
    return {"freeze": freeze, "hold_s": hold if freeze else None, "runs": len(runs),
            "cancel_http": count(r.get("cancel_http") for r in runs),
            "final": count(r.get("final") for r in runs), "stop_reason": count(r.get("stop_reason") for r in runs),
            "term_s": ts, "thawed_at_s": thawed_s}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--holds", default="6")
    ap.add_argument("--limit", type=int, default=90)
    ap.add_argument("--jitter", type=float, default=10.0)
    ap.add_argument("--note", default="")
    ap.add_argument("--cancel-after", type=float, default=0.0)
    a = ap.parse_args()
    holds = [float(x) for x in a.holds.split(",")]
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for i in range(a.trials):
            for name, base in (("A", A), ("B", B)):
                code = httpx.get(base + "/health/ready", timeout=5).status_code
                if code != 200:
                    raise SystemExit("预检失败: %s %s" % (name, code))
            if i and a.jitter:
                w = random.uniform(0, a.jitter)
                print("  随机等待 %.1f s" % w, flush=True)
                await asyncio.sleep(w)
            hold = holds[i % len(holds)]
            print("试验 %d/%d%s" % (i + 1, a.trials, (" 冻结 %.0f s" % hold) if a.freeze else ""), flush=True)
            res = await trial(client, a.freeze, hold, a.limit, a.n, a.cancel_after)
            print(json.dumps(res, ensure_ascii=False), flush=True)
            results.append(res)
    allt = [x for r in results for x in r["term_s"]]
    if allt:
        allt.sort()
        print("\n终态耗时(全部运行 n=%d): 最小 %.2f 中位 %.2f 最大 %.2f" % (len(allt), allt[0], allt[len(allt)//2], allt[-1]))
    d = os.path.join(REPO, "bench", "results")
    fn = os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + "_cancel.json")
    with open(fn, "w") as f:
        json.dump({"note": a.note, "freeze": a.freeze, "holds": a.holds, "cancel_after": a.cancel_after, "trials": results}, f, ensure_ascii=False, indent=2)
    print("已保存:", fn)


asyncio.run(main())
