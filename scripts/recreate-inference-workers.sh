#!/usr/bin/env bash
# 用最新 inference-lite-gpu 镜像重建正在运行的推理容器（不构建镜像）
set -euo pipefail
cd "$(dirname "$0")/.."

docker exec visual-dps-ui python3 -c "
import docker
from services.camera_store import load_cameras
from services.inference_container_service import start_inference_container, stop_inference_container

client = docker.from_env()
running_ids = {
    str((c.labels or {}).get('visual-dps.camera_id') or '').strip()
    for c in client.containers.list(all=True, filters={'label': 'visual-dps.role=inference'})
}
running_ids.discard('')

items = load_cameras('/app/localdata/camera_ips.json')
by_id = {str(c.get('id') or c.get('path') or '').strip(): c for c in items}

restarted = []
for cid in sorted(running_ids):
    cam = by_id.get(cid)
    if not cam:
        print(f'skip {cid}: no config')
        continue
    print(f'recreate {cid}...')
    stop_inference_container(cid)
    result = start_inference_container(cam)
    if result.get('error'):
        print(f'  ERROR: {result[\"error\"]}')
    else:
        restarted.append(cid)
        print(f'  OK')

print('done:', len(restarted), restarted)
"
