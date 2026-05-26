"""启动时探测 FFmpeg 硬件解码能力（NVIDIA 优先）。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class FfmpegDecodeProfile:
    name: str
    input_args: tuple[str, ...] = ()
    video_codec: str | None = None
    output_vf: str = "format=bgr24"

    @property
    def label(self) -> str:
        return self.name


def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"


def _run_lines(args: list[str], timeout: float = 4.0) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return ""


def _hwaccels() -> set[str]:
    text = _run_lines([_ffmpeg_bin(), "-hide_banner", "-hwaccels"])
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if line and not line.startswith("hardware"):
            out.add(line)
    return out


def _encoders() -> set[str]:
    text = _run_lines([_ffmpeg_bin(), "-hide_banner", "-encoders"])
    return {ln.split()[1] for ln in text.splitlines() if " c" in ln and len(ln.split()) >= 2}


def _decoders() -> set[str]:
    text = _run_lines([_ffmpeg_bin(), "-hide_banner", "-decoders"])
    return {ln.split()[1] for ln in text.splitlines() if " v" in ln and len(ln.split()) >= 2}


def _nvidia_visible() -> bool:
    if shutil.which("nvidia-smi"):
        try:
            proc = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True,
                timeout=2.0,
                check=False,
            )
            return proc.returncode == 0 and bool((proc.stdout or "").strip())
        except Exception:
            pass
    return os.path.exists("/dev/nvidia0")


@lru_cache(maxsize=1)
def probe_ffmpeg_decode_profile() -> FfmpegDecodeProfile:
    forced = os.environ.get("RTSP_DECODE_PROFILE", "").strip().lower()
    if forced in ("software", "cpu"):
        return FfmpegDecodeProfile(name="software")
    if forced in ("cuda", "nvidia", "cuvid"):
        return FfmpegDecodeProfile(
            name="cuda",
            input_args=("-hwaccel", "cuda", "-hwaccel_device", os.environ.get("RTSP_HWACCEL_DEVICE", "0")),
            video_codec="h264_cuvid",
            output_vf="hwdownload,format=bgr24",
        )

    hw = _hwaccels()
    dec = _decoders()

    if _nvidia_visible() and ("cuda" in hw or "cuvid" in hw):
        if "h264_cuvid" in dec:
            return FfmpegDecodeProfile(
                name="cuda",
                input_args=("-hwaccel", "cuda"),
                video_codec="h264_cuvid",
                output_vf="hwdownload,format=bgr24",
            )
        return FfmpegDecodeProfile(
            name="cuda",
            input_args=("-hwaccel", "cuda"),
            output_vf="hwdownload,format=bgr24",
        )

    if "qsv" in hw and "h264_qsv" in dec:
        return FfmpegDecodeProfile(
            name="qsv",
            input_args=("-hwaccel", "qsv"),
            video_codec="h264_qsv",
            output_vf="format=bgr24",
        )

    if "vaapi" in hw and os.path.exists("/dev/dri/renderD128"):
        return FfmpegDecodeProfile(
            name="vaapi",
            input_args=("-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128"),
            output_vf="format=bgr24",
        )

    return FfmpegDecodeProfile(name="software")


def probe_summary() -> str:
    p = probe_ffmpeg_decode_profile()
    return f"ffmpeg={_ffmpeg_bin()} decode={p.label}"
