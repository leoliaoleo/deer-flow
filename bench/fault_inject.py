#!/usr/bin/env python3
"""Fault injection: SIGKILL Gateway B while A and B both have runs in flight.
Per trial: how B's client streams break, how long until A reports B's runs terminal (and with
which status), when a reconnecting client's join on A reaches `end`, and whether A's own runs
survive. Token comes from the file named by DF_PAT_FILE and is never printed."""
import argparse, asyncio, json, os, re, socket, subprocess, time
from urllib.parse import urlparse
import httpx

A = os.environ.get("DF_A", "http://127.0.0.1:8001")
B = os.environ.get("DF_B", "http://127.0.0.1:8011")
REPO = os.path.expanduser("~/work/deer-flow")
SCRIPT = os.path.join(REPO, "scripts", "dev_multi_instance.sh")
TOK = open(os.path.expanduser(os.environ["DF_PAT_FILE"])).read().strip()
HDR = {"Authorization": "Bearer " + TOK}
TERMINAL = {"success", "error", "interrupted", "timeout", "cancelled", "canceled", "failed"}


def sh(*args):
    p = subprocess.run([SCRIPT, *args], capture_output=True, text=True, cwd=REPO)
    return p.returncode, (p.stdout + p.stderr).strip()[-300:]


def b_listening():
    u = urlparse(B)
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect((u.hostname, u.port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ownership_cfg():
    code = ("import yaml,json;c=yaml.safe_load(open('.deer-flow/multi-instance/config.yaml'));"
            "print(json.dumps(c.get('run_ownership')))")
    p = subprocess.run([REPO + "/backend/.venv/bin/python", "-c", code], capture_output=True, text=True, cwd=REPO)
    return p.stdout.strip()


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
            r["http"] = resp.status_code
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


async def watch_recovery(client, r, t_kill, timeout):
    last = None
    while time.time() - t_kill < timeout:
        st, d = await get_status(client, r)
        last = st
        if st in TERMINAL:
            r["rec_s"] = round(time.time() - t_kill, 1)
            r["final"] = st
            if d:
                r["info"] = {"keys": sorted(d.keys()),
                             **{k: str(d[k])[:120] for k in ("error", "stop_reason") if k in d}}
            return
        await asyncio.sleep(1.0)
    r["final"] = "timeout(last=%s)" % last


async def watch_join(client, r, t_kill, timeout):
    url = "%s/api/threads/%s/runs/%s/join" % (A, r["thread_id"], r["run_id"])
    try:
        async with client.stream("GET", url, headers={**HDR, "Accept": "text/event-stream"},
                                 timeout=httpx.Timeout(timeout + 5)) as resp:
            r["join_http"] = resp.status_code
            if resp.status_code != 200:
                return
            async for line in resp.aiter_lines():
                if line.startswith("event:") and line[6:].strip() == "end":
                    r["join_end_s"] = round(time.time() - t_kill, 1)
                    return
                if time.time() - t_kill > timeout:
                    return
    except Exception as e:
        r["join_err"] = type(e).__name__


async def trial(client, per_gw, timeout):
    rs = []
    for label, base in (("A", A), ("B", B)):
        for _ in range(per_gw):
            rs.append({"label": label, "base": base, "ready": asyncio.Event(), "thread_id": None,
                       "run_id": None, "ended": False, "broke": None, "http": None})
    tasks = [asyncio.create_task(drive(client, r)) for r in rs]
    try:
        await asyncio.wait_for(asyncio.gather(*[r["ready"].wait() for r in rs]), 20)
    except asyncio.TimeoutError:
        print("  警告: 20 秒内并非所有运行都开始输出", flush=True)
    await asyncio.sleep(1.0)
    inflight_b = sum(1 for r in rs if r["label"] == "B" and not r["ended"] and r["broke"] is None)
    rc, out = await asyncio.to_thread(sh, "stop", "b", "--kill")
    t_kill = time.time()
    print("  kill b: rc=%s %s" % (rc, out.splitlines()[-1] if out else ""), flush=True)
    await asyncio.gather(*tasks)
    a = [r for r in rs if r["label"] == "A"]
    bs_all = [r for r in rs if r["label"] == "B"]
    for r in a:
        if r["run_id"]:
            r["server_status"], _ = await get_status(client, r)
    bs = [r for r in bs_all if r["run_id"]]
    await asyncio.gather(*[watch_recovery(client, r, t_kill, timeout) for r in bs],
                         *[watch_join(client, r, t_kill, timeout) for r in bs])
    return {
        "a_client_ended": "%d/%d" % (sum(r["ended"] for r in a), len(a)),
        "a_server_status": count(r.get("server_status") for r in a),
        "b_inflight_at_kill": inflight_b,
        "b_port_closed_after_kill": not b_listening(),
        "b_stream_broke": count(r["broke"] for r in bs_all),
        "b_without_run_id": len(bs_all) - len(bs),
        "b_final_status": count(r.get("final") for r in bs),
        "b_recovered_s": sorted(r["rec_s"] for r in bs if r.get("rec_s") is not None),
        "b_join_end_s": sorted(r["join_end_s"] for r in bs if r.get("join_end_s") is not None),
        "b_join": count((r.get("join_http"), r.get("join_err")) for r in bs),
        "sample_info": next((r["info"] for r in bs if r.get("info")), None),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--per-gw", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    for name, base in (("A", A), ("B", B)):
        try:
            code = httpx.get(base + "/health/ready", timeout=5).status_code
        except Exception as e:
            raise SystemExit("预检失败: %s 连不上 (%s)" % (name, type(e).__name__))
        if code != 200:
            raise SystemExit("预检失败: %s /health/ready = %s" % (name, code))
    own = ownership_cfg()
    print("run_ownership:", own, flush=True)
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for i in range(a.trials):
            print("试验 %d/%d" % (i + 1, a.trials), flush=True)
            res = await trial(client, a.per_gw, a.timeout)
            print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
            results.append(res)
            await asyncio.to_thread(sh, "start", "b")
            for _ in range(60):
                try:
                    if httpx.get(B + "/health/ready", timeout=3).status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)
            else:
                print("B 没有恢复,停止")
                break
    d = os.path.join(REPO, "bench", "results")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + "_fault_kill_b.json")
    with open(fn, "w") as f:
        json.dump({"run_ownership": own, "per_gw": a.per_gw, "timeout": a.timeout, "note": a.note,
                   "trials": results}, f, ensure_ascii=False, indent=2)
    print("已保存:", fn)


asyncio.run(main())
