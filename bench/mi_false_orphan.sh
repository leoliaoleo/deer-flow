#!/usr/bin/env bash
# 用法: bash bench/mi_false_orphan.sh 每个Gateway并发 每个Gateway运行数
set -u
C="${1:-10}"; N="${2:-150}"
cd ~/work/deer-flow || exit 1
RUN=$(cat ~/mi_run_dir.txt); export DF_PAT_FILE="$RUN/pat"
for p in 8001 8011; do
  curl -s -m 3 "http://127.0.0.1:$p/health/ready" >/dev/null || { echo "Gateway :$p 不可用"; exit 1; }
done
python3 bench/mi_counts.py > /tmp/mi_before.json || exit 1
echo "前: $(cat /tmp/mi_before.json)"
DF_BASE=http://127.0.0.1:8001 ~/venvs/bench/bin/python bench/load.py -c "$C" -n "$N" --note "mi no-fault A c=$C n=$N" > /tmp/mi_load_a.txt 2>&1 &
PA=$!
DF_BASE=http://127.0.0.1:8011 ~/venvs/bench/bin/python bench/load.py -c "$C" -n "$N" --note "mi no-fault B c=$C n=$N" > /tmp/mi_load_b.txt 2>&1 &
PB=$!
wait "$PA" "$PB"
sleep 20
python3 bench/mi_counts.py > /tmp/mi_after.json
echo "后: $(cat /tmp/mi_after.json)"
python3 - <<'PY'
import json
b = json.load(open('/tmp/mi_before.json')); a = json.load(open('/tmp/mi_after.json'))
print('周期对账恢复数增量: A %d, B %d' % (a['recovered_a'] - b['recovered_a'], a['recovered_b'] - b['recovered_b']))
ks = sorted(set(a['runs']) | set(b['runs']))
print('runs 状态增量:', {k: a['runs'].get(k, 0) - b['runs'].get(k, 0) for k in ks})
for tag in ('a', 'b'):
    t = open('/tmp/mi_load_%s.txt' % tag).read()
    i = t.find('{\n')
    try:
        d = json.loads(t[i:])
        print(tag.upper(), '客户端 ok=%s/%s bad=%s 吞吐=%s p50=%s p95=%s rss=%s' % (
            d['ok'], d['n'], d['bad'], d['throughput_rps'], d['total_p50'], d['total_p95'], d['gateway_rss_mb']))
    except Exception as e:
        print(tag.upper(), '结果解析失败', e)
PY
