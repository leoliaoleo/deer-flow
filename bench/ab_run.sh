#!/usr/bin/env bash
# 用法: bash bench/ab_run.sh full|delta [运行数=300] [并发=10] [快照间隔=10]
# 依赖仓库外的 ~/bench-config/config.bench.yaml(只含 mock 模型,后台调用已关闭)
set -u
MODE="${1:-}"; N="${2:-300}"; C="${3:-10}"; SNAP="${4:-10}"
case "$MODE" in full|delta) ;; *) echo "用法: bash bench/ab_run.sh full|delta [运行数] [并发] [快照间隔]"; exit 1 ;; esac
REPO="$HOME/work/deer-flow"
RUN="$HOME/bench-runs/$(date +%Y%m%d_%H%M%S)_${MODE}"
OLD_DB="$REPO/backend/.deer-flow/data/deerflow.db"
BASE_CFG="$HOME/bench-config/config.bench.yaml"
cd "$REPO" || exit 1
fail() { echo "失败: $1"; make stop >/dev/null 2>&1; exit 1; }
[ -f "$BASE_CFG" ] || fail "缺少 $BASE_CFG"
mkdir -p "$RUN/data" "$RUN/home"; chmod 700 "$RUN"

# 1) 确保没有残留服务
make stop >/dev/null 2>&1; sleep 2
for p in 8001 3000 2026; do
  line=$(ss -ltnp "sport = :$p" | tail -n +2)
  if [ -n "$line" ]; then
    pid=$(echo "$line" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null)
    if [ "$p" = 3000 ] && [ "$cwd" = "$REPO/frontend" ]; then kill "$pid"; sleep 2
    else fail "端口 $p 被占用(进程 $pid,目录 $cwd),未处理"; fi
  fi
done

# 2) Windows 可用内存检查
W=$(powershell.exe -NoProfile -Command '[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)' 2>/dev/null | tr -d '\r')
if [ -n "$W" ]; then
  echo "Windows 可用内存: ${W} GB"
  awk -v w="$W" 'BEGIN{exit !(w+0 < 1.8)}' && fail "Windows 可用内存低于 1.8 GB,先关掉多余程序再试"
fi

# 3) mock
if ! curl -s -m 3 http://127.0.0.1:9100/health >/dev/null; then
  (cd bench/mock_llm && nohup ~/venvs/mockllm/bin/uvicorn server:app --host 127.0.0.1 --port 9100 >> ~/mock.log 2>&1 &)
  sleep 3
fi
curl -s -m 3 http://127.0.0.1:9100/health >/dev/null || fail "mock 起不来"

# 4) 生成隔离配置
MODE="$MODE" SNAP="$SNAP" RUN="$RUN" BASE_CFG="$BASE_CFG" backend/.venv/bin/python - <<'PY'
import os, yaml
c = yaml.safe_load(open(os.environ['BASE_CFG'], encoding='utf-8'))
db = c.setdefault('database', {})
db['backend'] = 'sqlite'
db['sqlite_dir'] = os.path.join(os.environ['RUN'], 'data')
db['checkpoint_channel_mode'] = os.environ['MODE']
if os.environ['MODE'] == 'delta':
    db.setdefault('checkpoint_delta', {})['snapshot_frequency'] = int(os.environ['SNAP'])
dst = os.path.join(os.environ['RUN'], 'config.yaml')
with open(dst, 'w', encoding='utf-8') as f:
    yaml.safe_dump(c, f, allow_unicode=True, sort_keys=False)
os.chmod(dst, 0o600)
print('database:', {k: db[k] for k in ('backend', 'sqlite_dir', 'checkpoint_channel_mode')})
print('models:', [m['name'] for m in c['models']])
PY
[ -f "$RUN/config.yaml" ] || fail "配置没有生成"

OLD_BEFORE=$(stat -c '%s %Y' "$OLD_DB" 2>/dev/null || echo none)
MOCK_BEFORE=$(grep -c MOCKREQ ~/mock.log)

# 5) 启动 Gateway(与之前基线相同的启动方式)
DEER_FLOW_CONFIG_PATH="$RUN/config.yaml" DEER_FLOW_HOME="$RUN/home" SKIP_FRONTEND_BUILD=1 make start-daemon > "$RUN/start.log" 2>&1
ok=0
for i in $(seq 1 60); do
  if curl -s -m 3 http://127.0.0.1:8001/api/v1/auth/setup-status | grep -q needs_setup; then ok=1; break; fi
  sleep 2
done
if [ "$ok" != 1 ]; then
  tail -n 15 logs/gateway.log | sed -E 's/sk-[A-Za-z0-9]+/sk-***/g' | cut -c1-180
  fail "Gateway 没起来"
fi

# 6) 管理员和令牌
python3 bench/ab_setup.py "$RUN" || fail "创建账号或令牌失败"

# 7) 压测
DF_PAT_FILE="$RUN/pat" ~/venvs/bench/bin/python bench/load.py -c "$C" -n "$N" --note "ab mode=$MODE snap=$SNAP fresh-db"

# 8) 停止并统计
make stop >/dev/null 2>&1; sleep 3
cp logs/gateway.log "$RUN/gateway.log" 2>/dev/null
echo "=== 汇总 mode=$MODE snap=$SNAP N=$N C=$C ==="
DB=$(find "$RUN/data" -name deerflow.db 2>/dev/null | head -1)
if [ -z "$DB" ]; then echo "没有找到新数据库"; else
python3 - "$DB" <<'PY'
import glob, os, sqlite3, sys
db = sys.argv[1]
tot = sum(os.path.getsize(p) for p in glob.glob(db + '*'))
print('数据库文件合计: %.1f MB(主文件 %.1f MB)' % (tot / 1048576, os.path.getsize(db) / 1048576))
con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
one = lambda q: con.execute(q).fetchone()[0]
runs = one('select count(*) from runs') or 1
cps = one('select count(*) from checkpoints')
print('runs=%d checkpoints=%d writes=%d' % (runs, cps, one('select count(*) from writes')))
print('checkpoint 列合计: %.1f MB' % ((one('select sum(length(checkpoint)) from checkpoints') or 0) / 1048576))
print('每次运行(含预热): %.1f 个检查点, %.1f KB' % (cps / runs, tot / 1024 / runs))
PY
fi
echo "mock 请求增量: $(( $(grep -c MOCKREQ ~/mock.log) - MOCK_BEFORE ))(预期 $((N+3)))"
echo "gateway 日志 ERROR 行数: $(grep -c ' - ERROR - ' "$RUN/gateway.log" 2>/dev/null)"
OLD_AFTER=$(stat -c '%s %Y' "$OLD_DB" 2>/dev/null || echo none)
[ "$OLD_BEFORE" = "$OLD_AFTER" ] && echo "旧数据库未被改动" || echo "警告: 旧数据库发生了变化"
echo "结果目录: $RUN"
