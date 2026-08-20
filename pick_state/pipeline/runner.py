"""按 configs/*.json 装配并跑完整 record：FeatureBank → PickState → BoxTrigger → Alarm。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pick_state.experts.action_gate import get_shared_action_gate
from pick_state.experts.linear_expert import LinearPickExpert
from pick_state.experts.rule_expert import RulePickExpert
from pick_state.features.action_temporal import ActionSequenceTracker
from pick_state.features.bank import FeatureBank
from pick_state.features.box_geometry import compute_pair_features
from pick_state.features.pair_temporal import PairTemporalTracker
from pick_state.pipeline.alarm import AlarmTracker
from pick_state.pipeline.box_trigger import BoxTrigger
from pick_state.pipeline.smooth import ScalarSmoother, SmoothConfig
from pick_state.pipeline.types import FrameContext, PickDecision, PipelineResult


def load_pipeline_config(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _build_scorer(name: str, cfg: dict[str, Any]):
    if name == "rule_expert":
        return RulePickExpert(cfg.get("rule_expert") or {})
    if name == "linear_expert":
        return LinearPickExpert(cfg.get("linear_expert") or {})
    raise ValueError(f"未知 scorer: {name}")


class PickStatePipeline:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        pick_cfg = config.get("pick_state") or {}
        box_cfg = config.get("box_trigger") or {}
        alarm_cfg = config.get("alarm") or {}

        self.threshold = float(pick_cfg.get("threshold", 0.5))
        self.require_pick = bool(box_cfg.get("require_pick_state", True))
        self.wrist_score_min = float(box_cfg.get("wrist_score_min", 0.3))
        self.pose_frame_interval = int(config.get("pose_frame_interval") or 2)

        self._score_smooth_cfg = SmoothConfig(**(pick_cfg.get("score_smooth") or {}))
        self._score_smoothers: dict[str, ScalarSmoother] = {}
        self.scorer = _build_scorer(str(pick_cfg.get("scorer") or "rule_expert"), pick_cfg)
        self.alarm = AlarmTracker(
            min_consecutive_frames=int(alarm_cfg.get("min_consecutive_frames", 3)),
            cooldown_frames=int(alarm_cfg.get("cooldown_frames", 0)),
        )

        # 配对模式：判定单元从「人」变成「人-货框」对，未配置时完全不影响原路径
        pair_cfg = config.get("pair_state") or {}
        self.pair_enabled = bool(pair_cfg.get("enabled"))
        self.pair_threshold = float(pair_cfg.get("threshold", 0.5))
        self._pair_smooth_cfg = SmoothConfig(**(pair_cfg.get("score_smooth") or {}))
        self._pair_smoothers: dict[str, ScalarSmoother] = {}
        self.pair_scorer = (
            _build_scorer(str(pair_cfg.get("scorer") or "linear_expert"), pair_cfg)
            if self.pair_enabled
            else None
        )
        self._pair_temporal: PairTemporalTracker | None = None

        # 动作门控（A）与邻框几何门控（B）：默认关闭，由配置开关
        self.action_gate = get_shared_action_gate(pair_cfg.get("action_gate") or {})
        box_gate = pair_cfg.get("box_gate") or {}
        self.box_gate_enabled = bool(box_gate.get("enabled"))
        self.box_depth_min = float(box_gate.get("depth_ratio_min", 0.0))
        self.box_center_max = float(box_gate.get("center_dist_max", 99.0))
        self._action_tracker: ActionSequenceTracker | None = None

    def configure_dims(self, *, infer_width: int, infer_height: int, video_fps: float) -> None:
        """时序 tracker 需要画面尺寸与帧率，run_record 里按 record 配置。"""
        self._pair_temporal = PairTemporalTracker(
            infer_width=infer_width, infer_height=infer_height, video_fps=video_fps
        )
        if self.action_gate.enabled:
            self._action_tracker = ActionSequenceTracker(
                window_frames=self.action_gate.window_frames,
                step=self.action_gate.step,
                infer_height=infer_height,
            )

    def reset_session(self) -> None:
        self._score_smoothers.clear()
        self._pair_smoothers.clear()
        self.scorer.reset()
        if self.pair_scorer is not None:
            self.pair_scorer.reset()
        if self._pair_temporal is not None:
            self._pair_temporal.reset()
        if self._action_tracker is not None:
            self._action_tracker.reset()
        self.alarm.reset()

    def _smoother(self, track_id: str) -> ScalarSmoother:
        if track_id not in self._score_smoothers:
            self._score_smoothers[track_id] = ScalarSmoother(self._score_smooth_cfg)
        return self._score_smoothers[track_id]

    def _pair_smoother(self, key: str) -> ScalarSmoother:
        if key not in self._pair_smoothers:
            self._pair_smoothers[key] = ScalarSmoother(self._pair_smooth_cfg)
        return self._pair_smoothers[key]

    def _decide(self, row: dict[str, Any]) -> PickDecision:
        track_id = str(row.get("person_track_id") or "0")
        raw_score, detail = self.scorer.score(row)
        smooth = self._smoother(track_id).update(raw_score)
        smooth_v = float(smooth if smooth is not None else raw_score)
        return PickDecision(
            person_track_id=track_id,
            score_raw=float(raw_score),
            score_smooth=smooth_v,
            is_picking=smooth_v >= self.threshold,
            expert=self.scorer.name,
            detail=detail,
        )

    def _process_frame_pairwise(
        self,
        ctx: FrameContext,
        feature_rows: list[dict[str, Any]],
        box_trigger: BoxTrigger,
        infer_height: int,
    ) -> PipelineResult:
        """逐个 (人, 货框) 对打分：手腕落在哪个框里，就只对那个框负责。"""
        decisions: list[PickDecision] = []
        tokens: set[str] = set()
        hit_detail: list[dict[str, Any]] = []

        if self._pair_temporal is None:
            self.configure_dims(
                infer_width=infer_height, infer_height=infer_height, video_fps=15.0
            )

        # 先枚举全帧的命中，才能让时序 tracker 知道哪些对本帧消失了
        pending: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
        active_pairs: dict[str, dict[str, Any]] = {}
        for row in feature_rows:
            person = row.get("_person")
            if not isinstance(person, dict):
                continue
            track_id = str(row.get("person_track_id") or "0")
            for hit in box_trigger.hits_for_person(person):
                box = hit.get("box")
                if box is None:
                    continue
                pair = compute_pair_features(person, hit, box, infer_height=infer_height)
                key = f"{track_id}|{hit['token']}"
                active_pairs[key] = {
                    "depth_ratio": hit["depth_ratio"],
                    "center_dist_norm": pair.get("center_dist_norm"),
                    "wrist_xy": hit["wrist_xy"],
                }
                pending.append((row, key, hit, pair))

        temporal_feats = self._pair_temporal.update(ctx.frame_idx, active_pairs)

        # 动作门控 A：无进框时只维护 warm track；有进框时本帧所有人仍写入（与改前进框帧一致）
        # GBDT 仅在 smooth >= 阈值后计算
        action_ok: dict[str, tuple[bool, float]] = {}
        hit_track_ids = {key.split("|", 1)[0] for _, key, _, _ in pending}
        if self.action_gate.enabled and self._action_tracker is not None:
            if hit_track_ids:
                self._action_tracker.update(ctx.frame_idx, feature_rows, track_ids=None)
            else:
                warm = self._action_tracker._warm_tracks(ctx.frame_idx)
                if warm:
                    self._action_tracker.update(ctx.frame_idx, feature_rows, track_ids=warm)

        for row, key, hit, pair in pending:
            pair_row = dict(row)
            pair_row.update(pair)
            pair_row.update(temporal_feats.get(key) or {})
            raw, detail = self.pair_scorer.score(pair_row)
            smooth = self._pair_smoother(key).update(raw)
            smooth_v = float(smooth if smooth is not None else raw)
            is_picking = smooth_v >= self.pair_threshold
            track_id = key.split("|", 1)[0]
            gate_detail: dict[str, Any] = {}
            if is_picking and self.action_gate.enabled and self._action_tracker is not None:
                if track_id not in action_ok:
                    feat = self._action_tracker.features(ctx.frame_idx, track_id)
                    action_ok[track_id] = self.action_gate.allow(feat)
                ok, act_p = action_ok[track_id]
                gate_detail["action_score"] = act_p
                if not ok:
                    is_picking = False
                    gate_detail["blocked_by"] = "action_gate"
            if is_picking and self.box_gate_enabled:
                depth = float(hit.get("depth_ratio") or pair.get("depth_ratio") or 0.0)
                center = float(pair.get("center_dist_norm") or 99.0)
                gate_detail["depth_ratio"] = depth
                gate_detail["center_dist_norm"] = center
                if depth < self.box_depth_min or center > self.box_center_max:
                    is_picking = False
                    gate_detail["blocked_by"] = "box_gate"
            if gate_detail:
                detail = dict(detail or {})
                detail["gates"] = gate_detail
            decisions.append(
                PickDecision(
                    person_track_id=key,
                    score_raw=float(raw),
                    score_smooth=smooth_v,
                    is_picking=is_picking,
                    expert=self.pair_scorer.name,
                    detail=detail,
                )
            )
            if is_picking:
                tokens.add(hit["token"])
                hit_detail.append(hit)

        collisions = sorted(tokens)
        return PipelineResult(
            frame_idx=ctx.frame_idx,
            pick_decisions=decisions,
            box_hits=collisions,
            alarm_hits=self.alarm.step(collisions, ctx.frame_idx),
            debug={"hits": hit_detail},
        )

    def process_frame(
        self,
        ctx: FrameContext,
        *,
        feature_rows: list[dict[str, Any]] | None = None,
        box_trigger: BoxTrigger | None = None,
        provisional_box_hits: list[str] | None = None,
        infer_height: int = 1,
    ) -> PipelineResult:
        """按人判定拣货态；非拣货态的人不贡献碰撞（对齐 DPS blocked → continue）。"""
        if self.pair_enabled and box_trigger is not None:
            return self._process_frame_pairwise(
                ctx, feature_rows or [], box_trigger, infer_height
            )

        decisions: list[PickDecision] = []
        tokens: set[str] = set()
        hit_detail: list[dict[str, Any]] = []

        for row in feature_rows or []:
            decision = self._decide(row)
            decisions.append(decision)
            if self.require_pick and not decision.is_picking:
                continue
            person = row.get("_person")
            if box_trigger is not None and isinstance(person, dict):
                for hit in box_trigger.hits_for_person(person):
                    tokens.add(hit["token"])
                    hit_detail.append(hit)

        if box_trigger is None:
            keep = provisional_box_hits or []
            if self.require_pick and not any(d.is_picking for d in decisions):
                keep = []
            tokens.update(keep)

        collisions = sorted(tokens)
        alarms = self.alarm.step(collisions, ctx.frame_idx)

        return PipelineResult(
            frame_idx=ctx.frame_idx,
            pick_decisions=decisions,
            box_hits=collisions,
            alarm_hits=alarms,
            debug={"hits": hit_detail},
        )

    def run_record(
        self,
        record,
        *,
        frame_indices: set[int] | None = None,
        on_frame=None,
    ) -> list[dict[str, Any]]:
        """跑完一条 record，返回与 collector 评估器兼容的 upload 行。"""
        infer_width = int(record.meta.get("infer_width") or record.ref.infer_width or 1)
        infer_height = int(record.meta.get("infer_height") or record.ref.infer_height or 1)
        self.configure_dims(
            infer_width=infer_width,
            infer_height=infer_height,
            video_fps=float(record.fps or 15.0),
        )
        self.reset_session()
        bank = FeatureBank(
            infer_width=infer_width,
            infer_height=infer_height,
            video_fps=float(record.fps or 15.0),
        )
        trigger = BoxTrigger(record.boxes, wrist_score_min=self.wrist_score_min)

        by_key: dict[int, dict[str, Any]] = {}
        for frame in record.frames:
            key = int(frame.get("source_frame_idx") or frame.get("frame_idx") or 0)
            by_key[key] = frame

        if frame_indices is not None:
            keys = sorted(frame_indices)
        else:
            keys = sorted(by_key)

        out: list[dict[str, Any]] = []
        for export_key in keys:
            # 无检测的帧仍需产出空行，否则告警连续帧计数与 baseline 不可比
            frame = by_key.get(export_key) or {
                "frame_idx": export_key,
                "source_frame_idx": export_key,
                "timestamp_sec": 0.0,
                "persons": [],
            }
            rows = bank.rows_for_frame(frame)
            ctx = FrameContext(
                record_id=record.ref.record_id,
                frame_idx=export_key,
                camera_slug=record.ref.camera_slug,
            )
            result = self.process_frame(
                ctx, feature_rows=rows, box_trigger=trigger, infer_height=infer_height
            )
            if on_frame is not None:
                on_frame(export_key, result)

            probs = [d.score_smooth for d in result.pick_decisions]
            out.append(
                {
                    "record_id": record.ref.record_id,
                    "frame_idx": export_key,
                    "is_picking": bool(result.alarm_hits),
                    "picking_prob": round(max(probs), 4) if probs else None,
                    "predicted_box_tokens": [],
                    "rule_collisions": result.box_hits,
                    "rule_alarm_collisions": result.alarm_hits,
                }
            )

        return out
