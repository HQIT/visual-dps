#!/usr/bin/env bash
# 按 logical shard 监控 pose:stream:{id} 的 lag 与消费速率。
set -euo pipefail

INTERVAL="${1:-300}"
LOGICAL_SHARDS="${POSE_LOGICAL_SHARD_COUNT:-16}"
REDIS_CONTAINER="${REDIS_CONTAINER:-visual-dps-redis}"
GROUP="${POSE_STREAM_GROUP:-event-workers}"
PREFIX="${POSE_STREAM_KEY_PREFIX:-pose:stream}"
DOCKER="${DOCKER:-sudo -n docker}"

if ! command -v date >/dev/null 2>&1; then
  echo "date required" >&2
  exit 1
fi

LOG_DIR="${LOG_DIR:-app/localdata/logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/pose-lag-$(date +%Y%m%d).log"

redis_cmd() {
  # shellcheck disable=SC2086
  $DOCKER exec "$REDIS_CONTAINER" redis-cli "$@"
}

parse_group_lag() {
  local stream_key="$1"
  redis_cmd XINFO GROUPS "$stream_key" 2>/dev/null | awk '
    /^name$/ { getline; name=$0 }
    /^lag$/ { getline; lag=$0 }
    END { if (name != "" && lag != "") print lag; else print "0" }
  ' | head -1
}

parse_entries_read() {
  local stream_key="$1"
  redis_cmd XINFO GROUPS "$stream_key" 2>/dev/null | awk '
    /^entries-read$/ { getline; print $0; exit }
  '
}

echo "ts,shard,stream,lag,consume_per_s,ingress_per_s,status" | tee -a "$LOG_FILE"
echo "# baseline $(date -Iseconds) shards=0-$((LOGICAL_SHARDS - 1)) interval=${INTERVAL}s" | tee -a "$LOG_FILE"

declare -A PREV_LAG PREV_READ PREV_TS

for ((sid = 0; sid < LOGICAL_SHARDS; sid++)); do
  stream="${PREFIX}:${sid}"
  lag="$(parse_group_lag "$stream" || echo 0)"
  read="$(parse_entries_read "$stream" || echo 0)"
  PREV_LAG[$sid]=${lag:-0}
  PREV_READ[$sid]=${read:-0}
  PREV_TS[$sid]=$(date +%s)
done

while true; do
  sleep "$INTERVAL"
  now=$(date +%s)
  for ((sid = 0; sid < LOGICAL_SHARDS; sid++)); do
    stream="${PREFIX}:${sid}"
    lag="$(parse_group_lag "$stream" || echo 0)"
    read="$(parse_entries_read "$stream" || echo 0)"
    dt=$((now - PREV_TS[$sid]))
    if (( dt <= 0 )); then
      continue
    fi
    dl=$((lag - PREV_LAG[$sid]))
    dr=$((read - PREV_READ[$sid]))
    consume=$(awk "BEGIN { printf \"%.2f\", $dr / $dt }")
    ingress=$(awk "BEGIN { printf \"%.2f\", ($dr + $dl) / $dt }")
    status="OK"
    if (( lag > 1500 )); then status="CRITICAL"; elif (( lag > 500 )); then status="WARN"; fi
    line="$(date -Iseconds),${sid},${stream},${lag},${consume},${ingress},${status}"
    echo "$line" | tee -a "$LOG_FILE"
    PREV_LAG[$sid]=$lag
    PREV_READ[$sid]=$read
    PREV_TS[$sid]=$now
  done
done
