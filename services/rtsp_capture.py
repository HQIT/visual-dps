"""RTSP 低延迟采帧：后台线程全速读流，仅保留最新帧；推理侧取快照副本。"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Tuple

import cv2
import numpy as np

from services.hwaccel_probe import probe_ffmpeg_decode_profile, probe_summary
from services.latest_frame_buffer import LatestFrameBuffer
from services.pipeline_log import get_inference_logger

_DEFAULT_FFMPEG_OPTS = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0"
_LOW_LATENCY_FFMPEG_OPTS = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", _DEFAULT_FFMPEG_OPTS)
_DRAIN_MAX = max(2, int(os.environ.get("RTSP_DRAIN_MAX", "12")))
_RTSP_RECONNECT_FAIL_STREAK = max(
    30, int(os.environ.get("RTSP_RECONNECT_FAIL_STREAK", "90"))
)


def apply_low_latency_ffmpeg_env() -> None:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _LOW_LATENCY_FFMPEG_OPTS


def _backend_mode() -> str:
    mode = os.environ.get("RTSP_CAPTURE_BACKEND", "auto").strip().lower()
    if mode in ("opencv", "ffmpeg"):
        return mode
    profile = probe_ffmpeg_decode_profile()
    if profile.name != "software":
        return "ffmpeg"
    if os.environ.get("RTSP_FFMPEG_FORCE", "").strip().lower() in ("1", "true", "yes"):
        return "ffmpeg"
    return "opencv"


class _FfmpegRtspCapture:
    """子进程 FFmpeg 解码 + 后台读帧线程 → LatestFrameBuffer。"""

    def __init__(self, url: str):
        self.url = url
        self._profile = probe_ffmpeg_decode_profile()
        self._width = 0
        self._height = 0
        self._proc: subprocess.Popen | None = None
        self._buffer = LatestFrameBuffer()
        self._opened = False
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()

    def isOpened(self) -> bool:
        return self._opened

    def snapshot_latest(self) -> Tuple[bool, np.ndarray | None, float]:
        ok, frame, captured_at, _seq = self._buffer.snapshot()
        return ok, frame, captured_at

    def _probe_size(self) -> tuple[int, int]:
        ffprobe = os.environ.get("FFPROBE_BIN", "ffprobe").strip() or "ffprobe"
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0:s=x",
                    self.url,
                ],
                capture_output=True,
                text=True,
                timeout=12.0,
                check=False,
            )
            line = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else ""
            if "x" in line:
                w, h = line.split("x", 1)
                return max(2, int(w)), max(2, int(h))
        except Exception:
            pass
        return 640, 360

    def _build_cmd(self) -> list[str]:
        ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-max_delay",
            "0",
        ]
        cmd.extend(self._profile.input_args)
        if self._profile.video_codec:
            cmd.extend(["-c:v", self._profile.video_codec])
        cmd.extend(["-i", self.url, "-an", "-vf", self._profile.output_vf])
        cmd.extend(
            [
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "pipe:1",
            ]
        )
        return cmd

    def open(self) -> bool:
        self._width, self._height = self._probe_size()
        cmd = self._build_cmd()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10**7,
            )
        except Exception:
            return False
        self._opened = True
        self._reader = threading.Thread(target=self._read_loop, name="ffmpeg-rtsp-reader", daemon=True)
        self._reader.start()
        return True

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        frame_bytes = self._width * self._height * 3
        stdout = self._proc.stdout
        while not self._stop.is_set():
            buf = stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape((self._height, self._width, 3))
            self._buffer.update(frame.copy())

    def grab(self) -> bool:
        ok, _, _, _ = self._buffer.snapshot()
        return ok

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        ok, frame, _, _ = self._buffer.snapshot()
        return ok, frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        deadline = time.time() + float(os.environ.get("RTSP_OPEN_TIMEOUT_SEC", "8"))
        while time.time() < deadline:
            ok, frame, _ = self.snapshot_latest()
            if ok and frame is not None:
                return True, frame
            time.sleep(0.02)
        return False, None

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop == cv2.CAP_PROP_FPS:
            return 25.0
        return 0.0

    def release(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._opened = False


class _OpencvRtspCapture:
    """OpenCV 采帧 + 后台 grab/retrieve 线程 → LatestFrameBuffer。"""

    def __init__(self, url: str, buffer_size: int = 1):
        self.url = url
        self._buffer_size = max(1, int(buffer_size))
        self._buffer = LatestFrameBuffer()
        self._cap: cv2.VideoCapture | None = None
        self._opened = False
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        apply_low_latency_ffmpeg_env()
        self._cv_cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            self._cv_cap.set(cv2.CAP_PROP_BUFFERSIZE, self._buffer_size)
        except Exception:
            pass

    def isOpened(self) -> bool:
        return self._opened

    def snapshot_latest(self) -> Tuple[bool, np.ndarray | None, float]:
        ok, frame, captured_at, _seq = self._buffer.snapshot()
        return ok, frame, captured_at

    def open(self) -> bool:
        if not self._cv_cap.isOpened():
            return False
        self._opened = True
        self._reader = threading.Thread(target=self._read_loop, name="opencv-rtsp-reader", daemon=True)
        self._reader.start()
        return True

    def _reconnect_cv_cap(self) -> cv2.VideoCapture | None:
        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception:
                pass
        apply_low_latency_ffmpeg_env()
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self._buffer_size)
        except Exception:
            pass
        self._cv_cap = cap
        return cap if cap.isOpened() else None

    def _read_loop(self) -> None:
        cap = self._cv_cap
        idle_sleep = 0.01
        fail_streak = 0
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                cap = self._reconnect_cv_cap()
                if cap is None:
                    time.sleep(0.5)
                    continue
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                self._buffer.update(frame.copy())
                fail_streak = 0
            else:
                fail_streak += 1
                if fail_streak >= _RTSP_RECONNECT_FAIL_STREAK:
                    get_inference_logger().warning(f"⚠️ RTSP 读帧连续失败，重连: {self.url}")
                    fail_streak = 0
                    cap = self._reconnect_cv_cap()
                    if cap is None:
                        time.sleep(0.5)
                    continue
                if fail_streak > 30:
                    time.sleep(0.05)
                else:
                    time.sleep(idle_sleep)

    def grab(self) -> bool:
        ok, _, _, _ = self._buffer.snapshot()
        return ok

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        ok, frame, _, _ = self._buffer.snapshot()
        return ok, frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        deadline = time.time() + float(os.environ.get("RTSP_OPEN_TIMEOUT_SEC", "8"))
        while time.time() < deadline:
            ok, frame, _ = self.snapshot_latest()
            if ok and frame is not None:
                return True, frame
            time.sleep(0.02)
        return False, None

    def get(self, prop: int) -> float:
        if self._cv_cap is None:
            return 0.0
        return float(self._cv_cap.get(prop))

    def set(self, prop: int, value: float) -> bool:
        if self._cv_cap is None:
            return False
        return bool(self._cv_cap.set(prop, value))

    def release(self) -> None:
        self._stop.set()
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)
        if self._cv_cap is not None:
            self._cv_cap.release()
            self._cv_cap = None
        self._opened = False


class _OpencvCaptureAdapter:
    """旧式同步适配（仅 read_rtsp_frame_once 等低频路径保留）。"""

    def __init__(self, cap: cv2.VideoCapture):
        self._cap = cap

    def isOpened(self) -> bool:
        return self._cap.isOpened()

    def grab(self) -> bool:
        return self._cap.grab()

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        return self._cap.retrieve()

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self._cap.read()

    def get(self, prop: int) -> float:
        return float(self._cap.get(prop))

    def set(self, prop: int, value: float) -> bool:
        return bool(self._cap.set(prop, value))

    def release(self) -> None:
        self._cap.release()


def open_rtsp_capture(url: str, buffer_size: int = 1):
    infer_log = get_inference_logger()
    mode = _backend_mode()
    if mode == "ffmpeg":
        cap = _FfmpegRtspCapture(url)
        if cap.open():
            ok, frame = cap.read()
            if ok and frame is not None:
                infer_log.info(f"ℹ️ RTSP 采帧: ffmpeg 后台最新帧 ({probe_summary()})")
                return cap
            cap.release()
            infer_log.warning(
                "⚠️ FFmpeg 采帧无首帧（常见：缺 libnvcuvid 导致 NVDEC 失败），回退 OpenCV"
            )
        else:
            infer_log.warning("⚠️ FFmpeg RTSP 打开失败，回退 OpenCV")

    cap = _OpencvRtspCapture(url, buffer_size=buffer_size)
    if cap.open():
        infer_log.info(f"ℹ️ RTSP 采帧: opencv 后台最新帧 ({probe_summary()})")
        return cap
    cap.release()
    infer_log.warning("⚠️ OpenCV 后台读帧启动失败，回退同步 OpenCV")
    apply_low_latency_ffmpeg_env()
    cv_cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cv_cap.set(cv2.CAP_PROP_BUFFERSIZE, max(1, int(buffer_size)))
    except Exception:
        pass
    return _OpencvCaptureAdapter(cv_cap)


def drain_capture_buffer(cap, max_grabs: int | None = None) -> int:
    """兼容旧调用；后台最新帧模式下为空操作。"""
    if hasattr(cap, "snapshot_latest"):
        return 0
    limit = _DRAIN_MAX if max_grabs is None else max(1, int(max_grabs))
    grabbed = 0
    for _ in range(limit):
        if not cap.grab():
            break
        grabbed += 1
    return grabbed


def read_latest_frame(cap, max_drain: int | None = None) -> Tuple[bool, np.ndarray | None, float]:
    if hasattr(cap, "snapshot_latest"):
        ok, frame, captured_at = cap.snapshot_latest()
        return ok, frame, captured_at
    limit = _DRAIN_MAX if max_drain is None else max(1, int(max_drain))
    grabbed = False
    for _ in range(limit):
        if not cap.grab():
            break
        grabbed = True
    if not grabbed:
        ok, frame = cap.read()
        return ok, frame, time.time()
    ok, frame = cap.retrieve()
    return ok, frame, time.time()


def read_rtsp_frame_once(url: str, timeout_sec: float | None = None) -> np.ndarray | None:
    """低频抓一帧（UI 缩略图/监控页）。勿启后台读帧线程，避免占满 UI 事件循环。"""
    if timeout_sec is not None:
        os.environ["RTSP_OPEN_TIMEOUT_SEC"] = str(timeout_sec)
    apply_low_latency_ffmpeg_env()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if not cap.isOpened():
            return None
        adapter = _OpencvCaptureAdapter(cap)
        try:
            deadline = time.time() + float(os.environ.get("RTSP_OPEN_TIMEOUT_SEC", "8"))
            while time.time() < deadline:
                ok, frame, _ = read_latest_frame(adapter, max_drain=12)
                if ok and frame is not None:
                    return frame
                time.sleep(0.05)
            return None
        finally:
            adapter.release()
    finally:
        cap.release()
