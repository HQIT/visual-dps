#!/usr/bin/env python3
"""本地跑通 pick_state pipeline（不写 event:live / 不打回调）。

用法（仓库根目录）:
  python scripts/run_pick_state_local.py
  python scripts/run_pick_state_local.py --config pick_state/configs/pipeline.v5_gated.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pick_state.boxes import box_from_polygon
from pick_state.features.bank import FeatureBank
from pick_state.pipeline.box_trigger import BoxTrigger
from pick_state.pipeline.runner import PickStatePipeline, load_pipeline_config
from pick_state.pipeline.types import FrameContext


def _synthetic_person(*, wrist_xy: tuple[float, float], track_id: str = "1") -> dict:
    """COCO-17：手腕放进货框内，其余点给合理占位。"""
    wx, wy = wrist_xy
    kpts = [[0.0, 0.0, 0.0] for _ in range(17)]
    # 肩/肘/腕大致一条线
    kpts[5] = [wx - 40, wy - 80, 0.9]  # L shoulder
    kpts[6] = [wx + 40, wy - 80, 0.9]
    kpts[7] = [wx - 20, wy - 40, 0.9]
    kpts[8] = [wx + 20, wy - 40, 0.9]
    kpts[9] = [wx, wy, 0.9]  # L wrist
    kpts[10] = [wx + 10, wy + 5, 0.85]  # R wrist
    kpts[11] = [wx - 30, wy + 60, 0.8]
    kpts[12] = [wx + 30, wy + 60, 0.8]
    kpts[13] = [wx - 30, wy + 120, 0.8]
    kpts[14] = [wx + 30, wy + 120, 0.8]
    kpts[15] = [wx - 30, wy + 160, 0.7]
    kpts[16] = [wx + 30, wy + 160, 0.7]
    return {"person_track_id": track_id, "person_id": int(track_id), "keypoints": kpts}


def main() -> int:
    ap = argparse.ArgumentParser(description="本地 smoke：pick_state v5_gated")
    ap.add_argument(
        "--config",
        default=str(ROOT / "pick_state/configs/pipeline.v5_gated.json"),
    )
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--out", default="", help="可选 JSONL 输出路径")
    ap.add_argument(
        "--keep-action-gate",
        action="store_true",
        help="保留配置里的动作门控（合成骨架通常会被挡，默认关闭以便 smoke）",
    )
    args = ap.parse_args()

    cfg = load_pipeline_config(args.config)
    if not args.keep_action_gate:
        pair = cfg.setdefault("pair_state", {})
        ag = pair.setdefault("action_gate", {})
        ag["enabled"] = False
    pipe = PickStatePipeline(cfg)
    ih, iw = 480, 852
    bank = FeatureBank(infer_width=iw, infer_height=ih, video_fps=15.0)
    pipe.configure_dims(infer_width=iw, infer_height=ih, video_fps=15.0)
    pipe.reset_session()
    bank.reset()

    # 货框覆盖画面中部
    box = box_from_polygon(
        "1001",
        [[300, 200], [500, 200], [500, 350], [300, 350]],
    )
    trigger = BoxTrigger([box], wrist_score_min=float((cfg.get("box_trigger") or {}).get("wrist_score_min", 0.15)))

    out_f = open(args.out, "w", encoding="utf-8") if args.out else None
    n_alarm = 0
    for i in range(1, args.frames + 1):
        # 前半段手在框外，后半段伸入
        if i <= args.frames // 2:
            person = _synthetic_person(wrist_xy=(100.0, 100.0))
        else:
            person = _synthetic_person(wrist_xy=(400.0, 270.0))
        frame = {"frame_idx": i, "persons": [person]}
        rows = bank.rows_for_frame(frame)
        # FeatureBank 可能不附 _person；对齐 runner 需要
        for r in rows:
            if "_person" not in r:
                tid = str(r.get("person_track_id") or "")
                for p in frame["persons"]:
                    if str(p.get("person_track_id")) == tid:
                        r["_person"] = p
        ctx = FrameContext(record_id="local-smoke", frame_idx=i)
        result = pipe.process_frame(
            ctx, feature_rows=rows, box_trigger=trigger, infer_height=ih
        )
        alarms = list(result.alarm_hits or [])
        if alarms:
            n_alarm += 1
        line = {
            "frame_idx": i,
            "box_hits": list(result.box_hits or []),
            "alarm_hits": alarms,
            "n_decisions": len(result.pick_decisions or []),
            "scores": [
                {
                    "key": d.person_track_id,
                    "raw": round(d.score_raw, 4),
                    "smooth": round(d.score_smooth, 4),
                    "is_picking": d.is_picking,
                    "detail": d.detail,
                }
                for d in (result.pick_decisions or [])
            ],
        }
        print(json.dumps(line, ensure_ascii=False))
        if out_f:
            out_f.write(json.dumps(line, ensure_ascii=False) + "\n")

    if out_f:
        out_f.close()
    print(f"[ok] frames={args.frames} alarm_frames={n_alarm} config={args.config}", file=sys.stderr)
    if n_alarm <= 0:
        print("[warn] 后半段伸入未产生 alarm，请检查模型/门槛", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
