# pick_state（自包含算法包）

从试验场 `visual-dps-pick-state` 并入的拣货态打分与门控，**运行时不依赖外部目录**。

- 配置：`configs/pipeline.v5_gated.json`
- 模型：`models/v5_base/model.json`、`models/action_gate_v1/model.joblib`
- 本地 smoke：`python scripts/run_pick_state_local.py`（仓库根；需 numpy / opencv / scikit-learn）

后续 `event-worker-2` 将调用 `pick_state.pipeline.runner.PickStatePipeline`。
