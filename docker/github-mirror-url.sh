#!/bin/sh
# 将 github.com 资源 URL 转为镜像加速地址（构建 Docker 时用）
# 用法: sh github-mirror-url.sh <PROXY_BASE> <URL>
# 例: sh github-mirror-url.sh https://ghfast.top https://github.com/.../file.tar.xz
# PROXY_BASE 留空则原样返回 URL

proxy="${1:-}"
url="${2:-}"
if [ -z "$url" ]; then
  echo "usage: $0 <proxy_base> <url>" >&2
  exit 1
fi

case "$url" in
  https://github.com/*|http://github.com/*)
    if [ -n "$proxy" ]; then
      proxy="${proxy%/}"
      printf '%s/%s\n' "$proxy" "$url"
    else
      printf '%s\n' "$url"
    fi
    ;;
  *)
    printf '%s\n' "$url"
    ;;
esac
