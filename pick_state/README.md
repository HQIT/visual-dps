# pick_state（自包含算法包）

从试验场 `visual-dps-pick-state` 并入的拣货态打分与门控，**运行时不依赖外部目录**。

| 项 | 路径 |
|----|------|
| 生产配置 | `configs/pipeline.v5_gated.json` |
| 容器冒烟配置 | `configs/pipeline.v5_smoke.json`（关 A/B） |
| 模型 | `models/v5_base/model.json`、`models/action_gate_v1/model.onnx`（生产默认 ONNX；`model.joblib` 保留 sklearn 回退） |

- 进程内 smoke：`python scripts/run_pick_state_local.py`
- Redis/容器 smoke：`python scripts/smoke_event_worker_2_redis.py`（见 `docs/DAILY-2026-08-12.md`）
- 线上入口：`event_worker_2.py` → `PickStatePipeline`
