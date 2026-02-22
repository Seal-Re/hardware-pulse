#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if [ -z "${PREFIX:-}" ]; then
  echo "ERROR: PREFIX is not set. This script is intended to run inside Termux." >&2
  exit 1
fi

EDGE_BIN="hardware-pulse-edge/target/release/hardware-pulse-edge"
CRAWLER_SCRIPT="hardware-pulse-crawler/crawler_wg_xianyu.py"
CRAWLER_CONFIG="hardware-pulse-crawler/config.yml"

LOG_DIR="./logs"
mkdir -p "$LOG_DIR"

DATA_DIR="./data"
mkdir -p "$DATA_DIR"

RUN_DIR="$PREFIX/var/run"
mkdir -p "$RUN_DIR"
PID_FILE="$RUN_DIR/hardware-pulse.pids"

EDGE_PID_FILE="$RUN_DIR/hardware-pulse-edge.pid"
CRAWLER_PID_FILE="$RUN_DIR/hardware-pulse-crawler.pid"

# --- 阶段 0: 核心依赖自检与自愈 ---
echo "[0/3] 环境依赖自检..."
for cmd in python curl nc; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "WARN: 缺失命令 $cmd，尝试自动修复安装..."
    pkg install -y python curl netcat-openbsd >/dev/null 2>&1
  fi
done

# --- 阶段 1: 业务进程清理 ---
echo "[1/3] 清理残留进程 (edge+crawler)..."
if [ -f "$PID_FILE" ]; then
  while read -r name pid; do
    [ -n "${pid:-}" ] || continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "- kill $name (pid=$pid)"
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done <"$PID_FILE"
  rm -f "$PID_FILE"
fi
pkill -9 -f "crawler_wg_xianyu\.py" >/dev/null 2>&1 || true
pkill -9 -f "hardware-pulse-edge" >/dev/null 2>&1 || true
pkill -9 -f "java.*hardware-pulse-backend" >/dev/null 2>&1 || true
pkill -9 -f "redis-server" >/dev/null 2>&1 || true
pkill -9 -f "postgres" >/dev/null 2>&1 || true

# --- 阶段 2: Redis 检查与自愈 ---
echo "[2/3] 启动 Rust edge (HTTP + SQLite + scheduler + ETL)..."
if [ ! -f "$EDGE_BIN" ]; then
  echo "ERROR: edge 二进制不存在: $EDGE_BIN" >&2
  echo "Termux 内构建: cd hardware-pulse-edge && pkg install -y rust && cargo build --release" >&2
  exit 1
fi

nohup "$EDGE_BIN" \
  --bind "127.0.0.1:8080" \
  --db-path "$DATA_DIR/hardware_pulse.db" \
  --memwatch-log "$LOG_DIR/mem_watch.log" \
  --memwatch-interval-ms 1000 \
  --pid-file "$EDGE_PID_FILE" \
  --crawler-pid-file "$CRAWLER_PID_FILE" \
  >"$LOG_DIR/edge.log" 2>&1 &
EDGE_PID=$!
echo "edge $EDGE_PID" >"$PID_FILE"

echo "Waiting edge /health (max 20s)..."
for i in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
    echo ">> edge is up"
    break
  fi
  sleep 1
done

echo "Seeding tasks from $CRAWLER_CONFIG thresholds..."
SEED_TMP="$RUN_DIR/edge_seed.json"
awk '
  /^thresholds:/{flag=1; next}
  flag && /^[^[:space:]]/{flag=0}
  flag && $0 ~ /^[[:space:]]{2}.+:[[:space:]]*/ {
    line=$0
    sub(/^[[:space:]]+/,"",line)
    split(line,a,":")
    key=a[1]
    gsub(/^"|"$/,"",key)
    gsub(/^\x27|\x27$/,"",key)
    print key
  }
' "$CRAWLER_CONFIG" | python - <<'PY' >"$SEED_TMP"
import sys, json
keywords=[line.strip() for line in sys.stdin if line.strip()]
print(json.dumps({"keywords": keywords, "initial_score": 50.0}, ensure_ascii=False))
PY

curl -sf -X POST "http://127.0.0.1:8080/api/spider/seed" \
  -H "Content-Type: application/json" \
  --data-binary "@$SEED_TMP" \
  >/dev/null 2>&1 || true

echo "[3/3] 启动 Python 爬虫..."
if [ ! -f "$CRAWLER_CONFIG" ]; then
  echo "ERROR: 爬虫配置文件不存在: $CRAWLER_CONFIG" >&2
  exit 1
fi
nohup python "$CRAWLER_SCRIPT" >"$LOG_DIR/crawler.log" 2>&1 &
CRAWLER_PID=$!
echo "crawler $CRAWLER_PID" >>"$PID_FILE"
echo "$CRAWLER_PID" >"$CRAWLER_PID_FILE"

# --- 健康检查 ---
echo "Checking :8080 (max 20s)..."
for i in $(seq 1 20); do
  if nc -z 127.0.0.1 8080 >/dev/null 2>&1; then
    echo ">> edge port open"
    break
  fi
  sleep 1
done

echo "---------------------------------------"
echo "Edge PID: $EDGE_PID"
echo "Crawler PID: $CRAWLER_PID"
echo "监控爬虫: tail -f $LOG_DIR/crawler.log"
echo "监控 edge: tail -f $LOG_DIR/edge.log"
echo "监控内存: tail -f $LOG_DIR/mem_watch.log"
echo "---------------------------------------"