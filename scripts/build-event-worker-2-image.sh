#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
# shellcheck disable=SC1091
source scripts/lib/load-build-env.sh
load_build_env "${ROOT}"
# shellcheck disable=SC1091
source scripts/lib/docker-image-tag.sh
# shellcheck disable=SC1091
source scripts/lib/sync-visual-dps-image-tag-env.sh

export VISUAL_DPS_IMAGE_TAG="${VISUAL_DPS_IMAGE_TAG:-$(visual_dps_image_tag)}"
REF="visual-dps-event-worker-2:${VISUAL_DPS_IMAGE_TAG}"

echo "==> 构建 ${REF} ..."
docker compose --profile worker-2 build visual-dps-event-worker-2

if [[ "${DOCKER_TAG_ALSO_LATEST:-0}" == "1" ]]; then
  docker tag "${REF}" visual-dps-event-worker-2:latest
  echo "  另打标签: visual-dps-event-worker-2:latest"
fi

sync_visual_dps_image_tag_env "${ROOT}" "${VISUAL_DPS_IMAGE_TAG}"
echo "OK: ${REF}"
