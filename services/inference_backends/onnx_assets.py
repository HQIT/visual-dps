"""下载并解压 OpenMMLab onnx_sdk 包，得到 end2end.onnx 本地路径。"""

from __future__ import annotations

import os
import shutil
import urllib.request
import zipfile

from services.pipeline_log import get_inference_logger


def ensure_onnx_from_zip(model_path: str, zip_url: str) -> str:
    """若 model_path 不存在则从 zip_url 下载并解压 end2end.onnx。"""
    model_path = os.path.abspath(model_path)
    if os.path.isfile(model_path):
        get_inference_logger().info(f"ℹ️ 使用本地 ONNX: {model_path}")
        return model_path

    os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
    tmp_zip = model_path + ".zip"
    get_inference_logger().info(f"⬇️ 正在下载 ONNX 模型包: {zip_url}")
    urllib.request.urlretrieve(zip_url, tmp_zip)

    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            onnx_members = [n for n in zf.namelist() if n.endswith("end2end.onnx")]
            if not onnx_members:
                raise RuntimeError(f"ZIP 中未找到 end2end.onnx: {zip_url}")
            with zf.open(onnx_members[0]) as src, open(model_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    finally:
        if os.path.isfile(tmp_zip):
            os.remove(tmp_zip)

    if not os.path.isfile(model_path):
        raise RuntimeError(f"ONNX 模型准备失败: {model_path}")
    return model_path
