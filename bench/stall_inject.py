#!/usr/bin/env python3
"""Freeze a HEALTHY Gateway B with SIGSTOP for S seconds, then SIGCONT, and count B's runs that the live
peer wrongly marks error/orphan_recovered. Token comes from DF_PAT_FILE and is never printed."""
import argparse, asyncio, json, os, random, re, signal, subprocess, time
import httpx

A = os.environ.get("DF_A", "http://127.0.0.1:8001")
B = os.environ.get("DF_B", "http://127.0.0.1:8011")
REPO = os.path.expanduser("~/work/deer-flow")
TOK = open(os.path.expanduser(os.environ["DF_PAT_FILE"])).read().strip()
HDR = {"Authorization": "Bearer " + TOK}
TERMINAL = {"success", "error", "interrupted", "timeout", "cancelled", "canceled", "failed"}


def gateway_pid(port):
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
        async with client.stream("POST", r["base"] + "/api/runs/stream", json=body,
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


async def get_status(client, r):
    try:
        resp = await client.get("%s/api/threads/%s/runs/%s" % (A, r["thread_id"], r["run_id"]), headers=HDR, timeout=10)
        if resp.status_code != 200:
            return "http%d" % resp.status_code, None
        d = resp.json()
        return d.get("status"), d
    except Exception as e:
        return "exc:" + type(e).__name__, None


async def trial(client, stall, per_gw, settle):
    rs = []
    for label, base in (("A", A), ("B", B)):
        for _ in range(per_gw):
            rs.append({"label": label, "base": base, "ready": asyncio.Event(), "thread_id": None,
                       "run_id": None, "ended": False, "broke": None})
    tasks = [asyncio.create_task(drive(client, r)) for r in rs]
    try:
        await asyncio.wait_for(asyncio.gather(*[r["ready"].wait() for r in rs]), 20)
    except asyncio.TimeoutError:
        print("  警告: 20 秒内并非所有运行都开始输出", flush=True)
    await asyncio.sleep(1.0)
    pid = gateway_pid(8011)
    if pid is None:
        for t in tasks:
            t.cancel()
        raise SystemExit("找不到 B 的进程")
    b_inflight = sum(1 for r in rs if r["label"] == "B" and not r["ended"] and r["broke"] is None)
    os.kill(pid, signal.SIGSTOP)
    try:
        await asyncio.sleep(stall)
    finally:
        os.kill(pid, signal.SIGCONT)
    done, pending = await asyncio.wait(tasks, timeout=settle)
    for t in pending:
        t.cancel()
    runs = [r for r in rs if r["run_id"]]
    final = {}
    deadline = time.time() + settle
    while time.time() < deadline:
        pend = [r for r in runs if r["run_id"] not in final]
        if not pend:
            break
        for r in pend:
            st, d = await get_status(client, r)
            if st in TERMINAL:
                final[r["run_id"]] = (st, (d or {}).get("stop_reason"))
        await asyncio.sleep(1.0)

    def agg(label, idx):
        return count(final.get(r["run_id"], ("pending", None))[idx] for r in runs if r["label"] == label)

    return {"stall_s": stall, "b_inflight_at_stall": b_inflight,
            "b_final": agg("B", 0), "b_stop_reason": agg("B", 1), "a_final": agg("A", 0),
            "b_streams_ended": sum(1 for r in rs if r["label"] == "B" and r["ended"]),
            "b_streams_broke": count(r["broke"] for r in rs if r["label"] == "B")}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stalls", default="10,18,22,26,34")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--per-gw", type=int, default=4)
    ap.add_argument("--settle", type=int, default=60)
    ap.add_argument("--jitter", type=float, default=15.0)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    schedule = [float(x) for x in a.stalls.split(",") for _ in range(a.reps)]
    random.shuffle(schedule)
    print("试验顺序(秒):", schedule, flush=True)
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for i, s in enumerate(schedule):
            for name, base in (("A", A), ("B", B)):
                try:
                    code = httpx.get(base + "/health/ready", timeout=5).status_code
                except Exception as e:
                    raise SystemExit("预检失败: %s (%s)" % (name, type(e).__name__))
                if code != 200:
                    raise SystemExit("预检失败: %s /health/ready = %s" % (name, code))
            if i > 0 and a.jitter:
                w = random.uniform(0, a.jitter)
                print("  随机等待 %.1f s" % w, flush=True)
                await asyncio.sleep(w)
            print("试验 %d/%d: 冻结 B %.0f s" % (i + 1, len(schedule), s), flush=True)
            res = await trial(client, s, a.per_gw, a.settle)
            print(json.dumps(res, ensure_ascii=False), flush=True)
            results.append(res)
    by = {}
    for t in results:
        by.setdefault(t["stall_s"], []).append(t)
    print("\n汇总(B 的运行出现非 success 的试验数 / 试验数):")
    for s in sorted(by):
        ts = by[s]
        hit = sum(1 for t in ts if any(k != "success" for k in t["b_final"]))
        print("  冻结 %4.0f s: %d/%d   各试验 B 的 error 数 %s" % (s, hit, len(ts), [t["b_final"].get("error", 0) for t in ts]))
    d = os.path.join(REPO, "bench", "results")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + "_stall_b.json")
    with open(fn, "w") as f:
        json.dump({"note": a.note, "stalls": a.stalls, "reps": a.reps, "trials": results}, f, ensure_ascii=False, indent=2)
    print("已保存:", fn)


asyncio.run(main())
