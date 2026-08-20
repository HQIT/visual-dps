#!/usr/bin/env python3
"""从 sample_stability_run.py 输出的 JSON 生成 Markdown 报告。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt_stats(d: dict, suffix: str = "") -> str:
    if not d:
        return "—"
    parts = []
    for k in ("min", "max", "avg", "median", "p95", "last"):
        if k in d:
            v = d[k]
            parts.append(f"{k} **{v}{suffix}**")
    return " · ".join(parts)


def _lag_series(samples: list[dict], width: int = 12) -> str:
    lags = [str(s["redis"]["lag"]) for s in samples]
    lines = []
    for i in range(0, len(lags), width):
        lines.append(" ".join(lags[i : i + width]))
    return "\n".join(lines)


def render(data: dict, json_name: str) -> str:
    obs = data["observation"]
    stack = data["stack"]
    redis = data["redis_pose_stream"]
    worker = data["worker2"]
    infer = data["infer"]
    gpu = data.get("gpu") or {}
    verdict = data["stability_verdict"]
    latency = data.get("latency_estimate") or {}
    samples = data.get("raw_samples") or []

    dur = obs["duration_sec"]
    dur_label = f"{dur // 60} min" if dur % 60 == 0 else f"{dur} s"
    routes = stack.get("active_infer_routes") or []
    n_infer = stack.get("infer_count") or infer.get("count", {}).get("last", "?")
    route_label = f"cam1–cam{n_infer}" if isinstance(n_infer, int) else ", ".join(routes[:3]) + "..."

    stable_icon = "稳定 ✓" if verdict.get("stable") else "不稳定 ✗（需关注）"
    lag = redis.get("lag") or {}
    pending = redis.get("pending") or {}
    wcpu = worker.get("cpu_pct") or {}
    icpu = infer.get("cpu_sum_pct") or {}
    gutil = gpu.get("util_pct") or {}
    gvram = gpu.get("vram_used_mib") or {}

    hs = obs.get("host_snapshot") or {}
    host_line = (
        f"{obs.get('host', '?')} · RTX 3090 · {hs.get('cpu_logical', '?')} 逻辑核 · "
        f"{hs.get('ram_total_gib', '?')} GiB RAM"
    )

    notes = stack.get("notes") or []
    notes_block = "\n".join(f"- {n}" for n in notes) if notes else ""

    compare_16 = (
        "| 维度 | 16 路 ORT 优化前 (0818 5min) | **16 路 ORT 优化后 (本报告)** |\n"
        "|------|------------------------------|-------------------------------|\n"
        f"| worker-2 CPU 均值 | ~194% | **~{wcpu.get('avg', '?')}%** |\n"
        f"| pose 消费速率 | ~64 msg/s | **~{redis.get('consume_rate_msg_per_s', '?')} msg/s** |\n"
        f"| Redis lag | avg **2.5** / max **10** | avg **{lag.get('avg', '?')}** / max **{lag.get('max', '?')}** |\n"
        f"| 推理 CPU 合计 | ~1008% | **~{icpu.get('avg', '?')}%** |\n"
        f"| GPU 利用率 | ~90% | **~{gutil.get('avg', '?')}%** |\n"
        f"| 稳定性 | 稳定 ✓ | **{stable_icon}** |"
    )

    verdict_notes = verdict.get("notes") or []
    verdict_detail = (
        "worker-2 消费与 pose 入流匹配良好，lag 处于低位。"
        if verdict.get("stable")
        else "；".join(verdict_notes) or "lag 或路数异常，需进一步排查。"
    )

    md = f"""# {n_infer} 路稳定运行 · {dur_label} 连续观测报告

**观测窗口**：{obs['started_at'][:19].replace('T', ' ')} – {obs['ended_at'][:19].replace('T', ' ')} CST（**{dur} s**）  
**采样间隔**：{obs['interval_sec']} s（共 **{obs['sample_count']}** 个样本）  
**主机**：{host_line}  
**场景**：{route_label} 推理 + 单 worker-2（`{stack.get('backend', 'rtmpose_m')}` · 镜像 `{stack.get('image_tag', '?')}`）

原始时序数据：[{json_name}](./{json_name})

---

## 1. 稳定性结论

| 判定 | **{stable_icon}** |
|------|-------------------|
| 准则 | {verdict.get('criteria', '—')} |
| 路数 | **{n_infer}** 路全程 {'不变 ✓' if len(set(s.get('infer_count') for s in samples)) <= 1 else '有变化 ✗'} |
| lag | {_fmt_stats(lag)} |
| pending | {_fmt_stats(pending)} |

**结论**：{verdict_detail}

---

## 2. Redis `pose:stream`

| 指标 | {dur_label} 统计 |
|------|------------------|
| 消费速率 | **{redis.get('consume_rate_msg_per_s', '?')} msg/s**（`entries-read` Δ {redis.get('entries_read_delta', '?')} / {dur - obs['interval_sec']:.0f} s 量级） |
| lag | {_fmt_stats(lag)} |
| pending | {_fmt_stats(pending)} |
| XLEN | **{redis.get('xlen_last', '?')}**（末样本） |

### lag 时序（每 {obs['interval_sec']} s）

```
{_lag_series(samples)}
```

---

## 3. 资源占用

### worker-2

| 指标 | {dur_label} |
|------|-------------|
| CPU | {_fmt_stats(wcpu, '%')} |
| 内存 | **~{worker.get('mem_mib_last', '?')} MiB**（末样本） |

### {n_infer}× 推理容器合计

| 指标 | {dur_label} / 末样本 |
|------|----------------------|
| CPU 合计 | {_fmt_stats(icpu, '%')} |
| 内存合计 | last **~{infer.get('mem_sum_mib_last', '?')} MiB** |

### GPU

| 指标 | {dur_label} |
|------|-------------|
| 利用率 | {_fmt_stats(gutil, '%')} |
| 显存 | **~{gvram.get('avg', gvram.get('last', '?'))} MiB** / {gpu.get('vram_used_mib', {}).get('last', '?')}（末样本 used） |
| compute 进程 | last **{gpu.get('compute_procs_last', '?')}** · infer VRAM 合计 **~{gpu.get('infer_vram_sum_mib_last', '?')} MiB** |

### 宿主机

| 项 | 观测初值 |
|----|----------|
| RAM | **{hs.get('ram_used_gib', '?')} / {hs.get('ram_total_gib', '?')} GiB** used |

---

## 4. 延迟（估算）

| 项 | 值 |
|----|-----|
| pose 帧间隔（15 fps ÷ interval 2） | **~{latency.get('pose_interval_ms', 133.3)} ms** |
| 队列附加延迟（lag 均值 ÷ 消费速率） | **~{(latency.get('queue_delay_ms_at_avg_lag') or 0) / 1000:.1f} s** |
| 队列附加延迟（lag 峰值） | **~{(latency.get('queue_delay_ms_at_max_lag') or 0) / 1000:.1f} s** |
| **端到端典型（估）** | **~{(latency.get('e2e_typical_ms') or 0) / 1000:.1f} s** |

---

## 5. 与历史观测对照（16 路）

{compare_16}

**本次变量**：

{notes_block}

**解读**：若 worker-2 CPU 均值显著低于 ORT 优化前 ~194%，而 lag 仍 ≤10，说明 **action_gate 共享 Session + ORT 线程收敛** 降低了无效 CPU 争抢，消费速率应仍 ~60–70 msg/s 量级。

---

## 6. 栈信息快照

| 项 | 值 |
|----|-----|
| 镜像 tag | `{stack.get('image_tag', '?')}` |
| 推理 backend | `{stack.get('backend', '?')}` |
| Event worker | `{stack.get('event_worker', '?')}` |
| 活跃路 | {route_label}（{n_infer} 容器） |
"""
    return md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    out = args.out or args.json_path.with_suffix(".md")
    out.write_text(render(data, args.json_path.name), encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
