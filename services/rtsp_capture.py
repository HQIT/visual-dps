"""RTSP 采帧：PyAV 进程内解码（FFmpeg 硬/软解自适应）。"""

from __future__ import annotations

import os
import time
from typing import Iterator, Tuple

import av
import numpy as np

from services.hwaccel_probe import FfmpegDecodeProfile, probe_ffmpeg_decode_profile, probe_summary

_DEFAULT_OPEN_OPTS = {
    "rtsp_transport": "tcp",
    "fflags": "nobuffer",
    "flags": "low_delay",
    "max_delay": "0",
    "stimeout": "5000000",
}


def _build_open_options(profile: FfmpegDecodeProfile) -> dict[str, str]:
    opts = dict(_DEFAULT_OPEN_OPTS)
    if profile.name == "software":
        return opts

    args = profile.input_args
    i = 0
    while i < len(args):
        key = args[i]
        if key == "-hwaccel" and i + 1 < len(args):
            opts["hwaccel"] = args[i + 1]
            i += 2
            continue
        if key == "-hwaccel_device" and i + 1 < len(args):
            opts["hwaccel_device"] = args[i + 1]
            i += 2
            continue
        i += 1

    device = os.environ.get("RTSP_HWACCEL_DEVICE", "").strip()
    if device and "hwaccel" in opts:
        opts["hwaccel_device"] = device

    if profile.video_codec:
        opts["codec"] = profile.video_codec

    return opts


def _profiles_to_try() -> list[FfmpegDecodeProfile]:
    forced = os.environ.get("RTSP_DECODE_PROFILE", "").strip().lower()
    if forced in ("software", "cpu"):
        return [FfmpegDecodeProfile(name="software")]

    primary = probe_ffmpeg_decode_profile()
    if primary.name == "software":
        return [primary]

    # 硬解优先，失败再软解
    return [
        primary,
        FfmpegDecodeProfile(name="software"),
    ]


def _frame_to_bgr(frame: av.VideoFrame) -> np.ndarray:
    return frame.to_ndarray(format="bgr24")


class PyAvRtspCapture:
    """推理会话内持久 RTSP 连接，按 frame_rate 节拍同步 read_frame()。"""

    def __init__(self, url: str):
        self.url = url
        self._container: av.container.InputContainer | None = None
        self._stream: av.video.stream.VideoStream | None = None
        self._decoder: Iterator[av.VideoFrame] | None = None
        self._width = 0
        self._height = 0
        self._fps = 25.0
        self._decode_label = "software"

    def isOpened(self) -> bool:
        return self._container is not None and self._stream is not None

    def _open_with_options(self, opts: dict[str, str], label: str) -> bool:
        self._container = av.open(
            self.url,
            mode="r",
            format="rtsp",
            options=opts,
            timeout=(float(os.environ.get("RTSP_OPEN_TIMEOUT_SEC", "8")), None),
        )
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"
        ctx = self._stream.codec_context
        self._width = int(ctx.width or 0)
        self._height = int(ctx.height or 0)
        if self._stream.average_rate:
            self._fps = float(self._stream.average_rate)
        elif self._stream.base_rate:
            self._fps = float(self._stream.base_rate)
        self._decoder = self._container.decode(video=0)
        self._decode_label = label
        return self._width > 0 and self._height > 0

    def open(self) -> bool:
        last_exc: Exception | None = None
        for profile in _profiles_to_try():
            opts = _build_open_options(profile)
            label = profile.name
            try:
                if self._open_with_options(opts, label):
                    print(f"ℹ️ RTSP 采帧: pyav decode={label} ({probe_summary()})")
                    return True
            except Exception as exc:
                last_exc = exc
                print(f"⚠️ PyAV RTSP 打开失败 ({label}): {exc}")
                self.release()
        if last_exc is not None:
            print(f"⚠️ PyAV RTSP 全部尝试失败: {last_exc}")
        return False

    def get(self, prop: int) -> float:
        import cv2

        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0

    def read_frame(self, timeout_sec: float | None = None) -> Tuple[bool, np.ndarray | None, float]:
        if not self.isOpened() or self._decoder is None:
            return False, None, time.time()
        if timeout_sec is None:
            timeout_sec = float(os.environ.get("RTSP_READ_TIMEOUT_SEC", "1.0"))
        deadline = time.time() + max(0.1, float(timeout_sec))
        while time.time() < deadline:
            try:
                frame = next(self._decoder)
            except StopIteration:
                break
            except av.AVError:
                break
            if frame is not None:
                return True, _frame_to_bgr(frame), time.time()
        return False, None, time.time()

    def release(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._stream = None
        self._decoder = None


def open_rtsp_capture(url: str, buffer_size: int = 1) -> PyAvRtspCapture:
    _ = buffer_size  # PyAV 无 OpenCV 式 buffer；节拍由推理侧 frame_rate 控制
    cap = PyAvRtspCapture(url)
    if not cap.open():
        raise RuntimeError(f"failed to open RTSP stream: {url}")
    return cap


def read_rtsp_frame_once(url: str, timeout_sec: float | None = None) -> np.ndarray | None:
    """按需读一帧（UI 缩略图等，与推理 pipeline 无关）。"""
    if timeout_sec is not None:
        prev = os.environ.get("RTSP_READ_TIMEOUT_SEC")
        os.environ["RTSP_READ_TIMEOUT_SEC"] = str(max(1.0, float(timeout_sec)))
        try:
            return _read_once(url)
        finally:
            if prev is None:
                os.environ.pop("RTSP_READ_TIMEOUT_SEC", None)
            else:
                os.environ["RTSP_READ_TIMEOUT_SEC"] = prev
    return _read_once(url)


def _read_once(url: str) -> np.ndarray | None:
    cap = PyAvRtspCapture(url)
    if not cap.open():
        return None
    try:
        ok, frame, _ = cap.read_frame()
        return frame if ok and frame is not None else None
    finally:
        cap.release()


def read_latest_frame(cap: PyAvRtspCapture) -> Tuple[bool, np.ndarray | None, float]:
    """兼容旧 inference_service 的 OpenCV 式 API。"""
    return cap.read_frame()


def drain_capture_buffer(cap: PyAvRtspCapture) -> None:
    """跳帧：解一帧丢弃（节拍由 frame_rate 控制，非 drain 积压）。"""
    cap.read_frame()


def probe_rtsp_available(url: str, timeout_sec: float = 6.0) -> bool:
    """探测 RTSP 是否可打开（不解码完整帧，用于在线状态）。"""
    opts = {
        "rtsp_transport": "tcp",
        "analyzeduration": "1000000",
        "probesize": "32768",
        "stimeout": str(int(max(1.0, timeout_sec) * 1_000_000)),
    }
    try:
        container = av.open(url, mode="r", format="rtsp", options=opts, timeout=(timeout_sec, None))
        ok = bool(container.streams.video)
        container.close()
        return ok
    except Exception:
        return False
