#!/usr/bin/env bash
# 现场部署 Docker 命令：无权限时自动 fallback 到 sudo docker。
# 强制 sudo: export VISUAL_DPS_DOCKER_SUDO=1
# 禁用 sudo: export VISUAL_DPS_DOCKER_SUDO=0

visual_dps_init_docker() {
  if [[ -n "${VISUAL_DPS_DOCKER_MODE:-}" ]]; then
    return 0
  fi
  local pref="${VISUAL_DPS_DOCKER_SUDO:-auto}"
  case "${pref}" in
    1|yes|true)
      VISUAL_DPS_DOCKER_MODE=sudo
      ;;
    0|no|false)
      VISUAL_DPS_DOCKER_MODE=plain
      ;;
    *)
      if docker info >/dev/null 2>&1; then
        VISUAL_DPS_DOCKER_MODE=plain
      elif sudo docker info >/dev/null 2>&1; then
        VISUAL_DPS_DOCKER_MODE=sudo
      else
        # 现场常见无 docker 组权限，默认按 sudo 尝试
        VISUAL_DPS_DOCKER_MODE=sudo
      fi
      ;;
  esac
  export VISUAL_DPS_DOCKER_MODE
}

docker_cmd() {
  visual_dps_init_docker
  if [[ "${VISUAL_DPS_DOCKER_MODE}" == sudo ]]; then
    sudo docker "$@"
  else
    docker "$@"
  fi
}

# 须预先设置 VISUAL_DPS_COMPOSE_FILE
compose_cmd() {
  visual_dps_init_docker
  local compose_file="${VISUAL_DPS_COMPOSE_FILE:-}"
  [[ -n "${compose_file}" ]] || {
    echo "错误: 未设置 VISUAL_DPS_COMPOSE_FILE" >&2
    exit 1
  }
  if docker_cmd compose version >/dev/null 2>&1; then
    docker_cmd compose -f "${compose_file}" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    if [[ "${VISUAL_DPS_DOCKER_MODE}" == sudo ]]; then
      sudo docker-compose -f "${compose_file}" "$@"
    else
      docker-compose -f "${compose_file}" "$@"
    fi
  else
    echo "错误: 需要 docker compose 或 docker-compose" >&2
    exit 1
  fi
}
