#!/usr/bin/env python3
"""验收 worker-2 action_gate 是否使用 ONNX 推理。

用法（容器内或仓库根）:
  python scripts/verify_worker2_action_gate.py
  docker exec visual-dps-event-worker-2 python scripts/verify_worker2_action_gate.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 容器内 docker exec 默认 cwd 可能不是 /app
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pick_state.experts.action_gate import (
    format_action_gate_probe_line,
    probe_action_gate_from_pipeline,
)


def main() -> int:
    cfg = os.environ.get(
        "PICK_STATE_CONFIG", "pick_state/configs/pipeline.v5_gated.json"
    ).strip()
    info = probe_action_gate_from_pipeline(cfg)
    print(format_action_gate_probe_line(info))
    print(json.dumps(info, ensure_ascii=False, indent=2))

    if not info.get("enabled"):
        print("SKIP: action_gate 未启用")
        return 0
    if info.get("backend") != "onnx":
        print("FAIL: backend 不是 onnx", file=sys.stderr)
        return 1
    if not info.get("session_ready"):
        print("FAIL: ONNX Session 未就绪", file=sys.stderr)
        return 1
    if not info.get("probe_ok"):
        print("FAIL: 探针试跑失败", file=sys.stderr)
        return 1
    print("OK: worker-2 action_gate 使用 ONNX")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
