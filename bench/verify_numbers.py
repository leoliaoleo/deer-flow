#!/usr/bin/env python3
"""Recompute the headline numbers in RESUME.md / bench/fault_kill_b.md from bench/results/*.json."""
import glob, json, os, statistics as st

R = os.path.expanduser("~/work/deer-flow/bench/results")
bad = []


def load(pat):
    return [(os.path.basename(p), json.load(open(p))) for p in sorted(glob.glob(os.path.join(R, pat)))]


def eq(name, got, want, tol=0.0):
    ok = abs(got - want) <= tol
    print("  %s %-46s 重算=%s 文档=%s" % ("OK  " if ok else "FAIL", name, round(got, 3), want))
    if not ok:
        bad.append(name)


def le(name, got, limit):
    ok = got <= limit + 1e-9
    print("  %s %-46s 重算=%s 文档上限=%s" % ("OK  " if ok else "FAIL", name, round(got, 3), limit))
    if not ok:
        bad.append(name)


def tmin(t):
    v = t.get("b_recovered_s")
    return min(v) if v else None


def stats(vals):
    return len(vals), st.mean(vals), (st.stdev(vals) if len(vals) > 1 else 0.0), min(vals), max(vals)


print("== 杀实例试验 ==")
kills = load("*_fault_kill_b.json")
trials = []  # (file, note, lease, is_pttl, trial)
for fn, d in kills:
    try:
        lease = json.loads(d["run_ownership"])["lease_seconds"]
    except Exception:
        lease = None
    for t in d["trials"]:
        trials.append((fn, d.get("note", ""), lease, bool(d.get("pttl")), t))
print("  文件数 %d, 试验数 %d, 各 lease 的试验数 %s" % (
    len(kills), len(trials), {k: sum(1 for x in trials if x[2] == k) for k in set(x[2] for x in trials)}))
eq("试验总数", len(trials), 47)
eq("lease 30 试验数", sum(1 for x in trials if x[2] == 30), 12)
eq("lease 15 试验数", sum(1 for x in trials if x[2] == 15), 35)
eq("B 的运行数", sum(sum(x[4]["b_final_status"].values()) for x in trials), 188)
eq("B 的 error 数", sum(x[4]["b_final_status"].get("error", 0) for x in trials), 188)
eq("A 的运行数", sum(sum(x[4]["a_server_status"].values()) for x in trials), 188)
eq("A 的 success 数", sum(x[4]["a_server_status"].get("success", 0) for x in trials), 188)

l30 = [tmin(x[4]) for x in trials if x[2] == 30 and tmin(x[4]) is not None]
n, m, s, lo, hi = stats(l30)
eq("lease 30: 样本数", n, 12)
eq("lease 30: 恢复耗时均值(s)", m, 53.8, 0.05)

l15 = [tmin(x[4]) for x in trials if x[2] == 15 and not x[3] and tmin(x[4]) is not None]
n, m, s, lo, hi = stats(l15)
eq("lease 15(非 PTTL): 样本数", n, 27)
eq("lease 15(非 PTTL): 均值(s),文档称约 25", m, 25.0, 0.5)

notes18 = ("kill b x8 lease15 grace5", "lease15 wait5", "lease15 wait30")
f18 = [tmin(x[4]) for x in trials if x[1] in notes18 and tmin(x[4]) is not None]
n, m, s, lo, hi = stats(f18)
eq("lease 15 前 18 次: 样本数", n, 18)
eq("lease 15 前 18 次: 均值", m, 25.1, 0.1)
eq("lease 15 前 18 次: 标准差", s, 4.5, 0.1)
eq("lease 15 前 18 次: 最小", lo, 18.5, 0.05)
eq("lease 15 前 18 次: 最大", hi, 32.8, 0.05)

ej_files = {}
for x in trials:
    if x[1] == "lease15 early-join":
        ej_files.setdefault(x[0], []).append(x[4])
ej8 = [v for v in ej_files.values() if len(v) == 8]
eq("early-join 8 次的结果文件数", len(ej8), 1)
if ej8:
    t8 = ej8[0]
    early = [t for t in t8 if min(t["b_recovered_s"]) < 27]
    late = [t for t in t8 if min(t["b_recovered_s"]) >= 27]
    eq("early-join: 早恢复(<27 s)试验数", len(early), 5)
    eq("early-join: 早恢复 join_end 最小", min(min(t["b_join_end_s"]) for t in early), 30.2, 0.05)
    eq("early-join: 早恢复 join_end 最大", max(max(t["b_join_end_s"]) for t in early), 30.5, 0.05)
    le("early-join: 晚恢复 |join_end-恢复| 最大", max(abs(min(t["b_join_end_s"]) - min(t["b_recovered_s"])) for t in late), 0.4)

print("== 冻结 ==")
agg = {}
for fn, d in load("*_stall_b.json"):
    for t in d["trials"]:
        agg.setdefault(t["stall_s"], []).append(t)


def hit(t):
    return any(k != "success" for k in t["b_final"])


low = [t for s_, ts in agg.items() if s_ <= 12 for t in ts]
high = [t for s_, ts in agg.items() if s_ >= 14 for t in ts]
eq("冻结 <=12 s: 试验数", len(low), 6)
eq("冻结 <=12 s: 误判试验数", sum(hit(t) for t in low), 0)
eq("冻结 >=14 s: 试验数", len(high), 18)
eq("冻结 >=14 s: 误判试验数", sum(hit(t) for t in high), 18)
eq(">=14 s 试验中 B 的 error 运行数", sum(t["b_final"].get("error", 0) for t in high), 72)
eq("<=12 s 试验中 B 的 success 运行数", sum(t["b_final"].get("success", 0) for t in low), 24)
eq("全部冻结试验中 A 的 success 运行数", sum(t["a_final"].get("success", 0) for t in low + high), 96)

print("== PTTL ==")
pt = [(fn, d) for fn, d in kills if d.get("pttl")]
eq("PTTL 结果文件数", len(pt), 1)
if pt:
    pred = {0.0: 27, 3.0: 24, 6.0: 21, 8.0: 29}
    devs, rules = [], []
    for t in pt[0][1]["trials"]:
        gone = [r["gone_s"] for r in t["pttl_rule"] if r.get("gone_s") is not None]
        devs.append(abs(min(gone) - pred[t["extra_delay_s"]]))
        rules += [(r["pred_publish_failed"], r["obs_publish_failed"]) for r in t["pttl_rule"]]
    eq("PTTL: 试验数", len(devs), 8)
    le("PTTL: gone_s 与预测最大偏差(预测窗 1.5 s)", max(devs), 1.5)
    eq("PTTL: gone_s 与预测最大偏差(文档已更正为 0.54 s)", max(devs), 0.54, 0.005)
    eq("PTTL: 运行总数", len(rules), 32)
    eq("PTTL: 模糊区运行数", sum(1 for p in rules if p[0] == "ambiguous"), 13)
    eq("PTTL: 预测补写成功的运行数", sum(1 for p in rules if p[0] is False), 15)
    eq("PTTL: 预测补写失败的运行数", sum(1 for p in rules if p[0] is True), 4)
    eq("PTTL: 预测与观测相反的运行数", sum(1 for p in rules if p[0] in (True, False) and p[0] != p[1]), 0)

print("== 无故障并发压测 ==")
nf = [d for fn, d in load("*.json") if isinstance(d, dict) and "mi no-fault" in d.get("note", "")]
eq("无故障压测结果文件数", len(nf), 4)
eq("客户端 ok 总数", sum(d["ok"] for d in nf), 900)
eq("客户端 bad 总数", sum(sum(d["bad"].values()) for d in nf), 0)
print("  说明: 库中 912 = 900 + 12 次预热(4 个压测进程各 3 次);此数字无法从 JSON 重算")

print("== 取消延迟 ==")
cs = load("*_cancel.json")
healthy = [d for fn, d in cs if not d.get("freeze")]
frozen = [d for fn, d in cs if d.get("freeze") and not d.get("cancel_after")]
expired = [d for fn, d in cs if d.get("freeze") and d.get("cancel_after")]
eq("取消结果文件总数", len(cs), 4)
eq("健康 B 文件数", len(healthy), 1)
eq("冻结(取消时租约有效)文件数", len(frozen), 1)
eq("租约已过期文件数", len(expired), 2)
if len(healthy) == 1:
    ts = sorted(x for t in healthy[0]["trials"] for x in t["term_s"])
    eq("健康 B: 试验数", len(healthy[0]["trials"]), 8)
    eq("健康 B: 运行数", len(ts), 32)
    eq("健康 B: 最小(s)", ts[0], 0.64, 0.005)
    eq("健康 B: 中位(s,第 17 个)", ts[len(ts) // 2], 2.56, 0.005)
    eq("健康 B: 最大(s)", ts[-1], 5.33, 0.005)
    eq("健康 B: interrupted 运行数", sum(t["final"].get("interrupted", 0) for t in healthy[0]["trials"]), 32)
if len(frozen) == 1:
    tr = frozen[0]["trials"]
    h6 = [t for t in tr if t["hold_s"] == 6.0]
    h20 = [t for t in tr if t["hold_s"] == 20.0]
    eq("冻结 6 s: 试验数", len(h6), 3)
    eq("冻结 6 s: interrupted 运行数", sum(t["final"].get("interrupted", 0) for t in h6), 12)
    eq("冻结 6 s: 终态最小(s)", min(x for t in h6 for x in t["term_s"]), 5.98, 0.005)
    eq("冻结 6 s: 终态最大(s)", max(x for t in h6 for x in t["term_s"]), 6.31, 0.005)
    eq("冻结 20 s: 试验数", len(h20), 3)
    eq("冻结 20 s: error 运行数", sum(t["final"].get("error", 0) for t in h20), 12)
    eq("冻结 20 s: 终态最小(s)", min(x for t in h20 for x in t["term_s"]), 20.27, 0.005)
    eq("冻结 20 s: 终态最大(s)", max(x for t in h20 for x in t["term_s"]), 30.28, 0.005)
if expired:
    tr = [t for d in expired for t in d["trials"]]
    allt = [x for t in tr for x in t["term_s"]]
    eq("租约已过期: 试验数", len(tr), 12)
    eq("租约已过期: 运行数", len(allt), 48)
    eq("租约已过期: error 运行数", sum(t["final"].get("error", 0) for t in tr), 48)
    eq("租约已过期: 终态最小(s)", min(allt), 0.05, 0.005)
    eq("租约已过期: 终态最大(s)", max(allt), 0.16, 0.005)
    eq("租约已过期: 返回 202 的试验数", sum(1 for t in tr if "202" in t["cancel_http"]), 8)
    eq("租约已过期: 返回 409 的试验数", sum(1 for t in tr if "409" in t["cancel_http"]), 4)

print("\n不一致项: %d" % len(bad))
for b in bad:
    print("  - " + b)
