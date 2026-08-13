#!/usr/bin/env bash
# install.sh 共用：按模式启动 compose 栈（须已定义 compose_cmd）
# WORKER_2=1 时只启 worker-2，停 worker-1；默认启 worker-1，停 worker-2

visual_dps_compose_up_stack() {
  local compose_file="${1:-}"
  local worker2="${WORKER_2:-0}"

  if [[ "${worker2}" -eq 1 ]]; then
    echo "==> 启动基础栈 + UI（worker-2 / pick_state，不启 worker-1）"
    compose_cmd up -d redis mediamtx visual-dps-ui
    compose_cmd stop visual-dps-event-worker 2>/dev/null || true
    compose_cmd --profile worker-2 up -d visual-dps-event-worker-2
    echo "ℹ️ 已启 visual-dps-event-worker-2；勿与 visual-dps-event-worker 同时运行"
  else
    echo "==> 启动服务（默认 worker-1 硬规则）"
    compose_cmd up -d
    compose_cmd --profile worker-2 stop visual-dps-event-worker-2 2>/dev/null || true
    echo "ℹ️ 切换 pick_state: install.sh --worker-2 或见 docs/DEPLOY-EVENT-WORKER-2.md"
  fi
}
