"""
图片输入与预处理模块。

支持格式：PNG、JPG/JPEG；自动拒绝 webp、heic、gif、psd。
忽略 ICC 色彩配置文件，仅读取基础 RGB 值。
PNG 透明像素标记为不生成方块，不参与匹配。

所有预处理操作在方块匹配之前生效，不修改原文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageOps


# ----------------------- 异常 -----------------------

class ImageInputError(Exception):
    """图片输入/预处理相关错误。"""


# ----------------------- 配置 -----------------------

@dataclass
class PreprocessConfig:
    crop: Optional[Tuple[int, int, int, int]] = None  # (left, top, right, bottom)
    rotate90: int = 0  # 0/1/2/3 表示顺时针旋转 0/90/180/270
    flip_horizontal: bool = False
    flip_vertical: bool = False
    brightness: float = 1.0  # 1.0 不变
    contrast: float = 1.0    # 1.0 不变
    scale: float = 1.0       # 等比例缩放因子，1.0 不变


@dataclass
class LoadResult:
    image: Image.Image  # PIL Image（RGB，透明已处理）
    alpha_mask: Optional[np.ndarray]  # (H, W) bool，True=不透明需生成方块，False=透明跳过
    size_bytes: int
    format: str  # "PNG" / "JPEG"
    width: int
    height: int
    warnings: list = field(default_factory=list)  # 提示信息（如文件过大）


# ----------------------- 输入 -----------------------

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
REJECTED_EXTENSIONS = {".webp", ".heic", ".gif", ".psd"}

LARGE_FILE_THRESHOLD = 5 * 1024 * 1024  # 5MB

# 与 __init__.py 导出名保持一致的别名
SUPPORTED_IMAGE_EXTENSIONS = SUPPORTED_EXTENSIONS
REJECTED_IMAGE_EXTENSIONS = REJECTED_EXTENSIONS


def _normalize_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def load_image(path: str) -> LoadResult:
    """读取图片，返回 LoadResult。

    - 拒绝 webp/heic/gif/psd 并提示
    - 忽略 ICC 配置文件（convert('RGB') 丢弃 ICC）
    - PNG 透明通道提取为 alpha_mask
    - 文件大于 5MB 输出提示
    """
    ext = _normalize_ext(path)
    if ext in REJECTED_EXTENSIONS:
        raise ImageInputError(
            f"不支持的图片格式：{ext}。请使用 PNG 或 JPG/JPEG 格式。"
        )
    if ext not in SUPPORTED_EXTENSIONS:
        raise ImageInputError(
            f"未知的图片扩展名：{ext}，仅支持 PNG、JPG/JPEG。"
        )

    size_bytes = os.path.getsize(path)
    warnings: list = []
    if size_bytes > LARGE_FILE_THRESHOLD:
        warnings.append(f"图片文件较大（{size_bytes / 1024 / 1024:.1f} MB），处理可能较慢。")

    img = Image.open(path)
    fmt = img.format or ("PNG" if ext == ".png" else "JPEG")

    # 忽略 ICC：convert 到 RGB，PIL 在 convert 时会丢弃嵌入的 ICC 配置文件，
    # 仅保留基础 RGB 值进行计算。
    if img.mode == "RGBA" or img.mode == "LA" or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        alpha = img.split()[3]
        alpha_mask = np.array(alpha) > 0  # True = 不透明
        img = img.convert("RGB")  # 丢弃 alpha，只保留 RGB
    else:
        img = img.convert("RGB")
        alpha_mask = np.ones((img.height, img.width), dtype=bool)

    return LoadResult(
        image=img,
        alpha_mask=alpha_mask,
        size_bytes=size_bytes,
        format=fmt,
        width=img.width,
        height=img.height,
        warnings=warnings,
    )


def to_array(img: Image.Image) -> np.ndarray:
    """PIL Image -> (H, W, 3) uint8 numpy 数组（RGB）。"""
    return np.array(img)


def apply_preprocess(arr: np.ndarray, alpha_mask: Optional[np.ndarray], cfg: PreprocessConfig):
    """对图像数组应用所有预处理操作，不修改原文件。

    顺序：裁剪 -> 旋转 -> 镜像 -> 亮度/对比度 -> 缩放。
    返回 (处理后的数组, 处理后的 alpha_mask)。
    """
    # 裁剪
    if cfg.crop is not None:
        left, top, right, bottom = cfg.crop
        left = max(0, min(left, arr.shape[1]))
        top = max(0, min(top, arr.shape[0]))
        right = max(left + 1, min(right, arr.shape[1]))
        bottom = max(top + 1, min(bottom, arr.shape[0]))
        arr = arr[top:bottom, left:right]
        if alpha_mask is not None:
            alpha_mask = alpha_mask[top:bottom, left:right]

    # 旋转 90 度
    if cfg.rotate90 == 1:
        arr = np.rot90(arr, k=1)  # 逆时针 90
        if alpha_mask is not None:
            alpha_mask = np.rot90(alpha_mask, k=1)
    elif cfg.rotate90 == 2:
        arr = np.rot90(arr, k=2)
        if alpha_mask is not None:
            alpha_mask = np.rot90(alpha_mask, k=2)
    elif cfg.rotate90 == 3:
        arr = np.rot90(arr, k=3)
        if alpha_mask is not None:
            alpha_mask = np.rot90(alpha_mask, k=3)

    # 镜像
    if cfg.flip_horizontal:
        arr = np.fliplr(arr)
        if alpha_mask is not None:
            alpha_mask = np.fliplr(alpha_mask)
    if cfg.flip_vertical:
        arr = np.flipud(arr)
        if alpha_mask is not None:
            alpha_mask = np.flipud(alpha_mask)

    # 亮度 / 对比度（在 PIL 上做更准确，这里用 numpy 近似，值域 0-255）
    if cfg.brightness != 1.0 or cfg.contrast != 1.0:
        # 对比度先于亮度，均以 128 为中心
        a = arr.astype(np.float32)
        if cfg.contrast != 1.0:
            a = (a - 128.0) * cfg.contrast + 128.0
        if cfg.brightness != 1.0:
            a = a * cfg.brightness
        arr = np.clip(a, 0, 255).astype(np.uint8)

    # 等比例缩放
    if cfg.scale != 1.0 and cfg.scale > 0:
        from PIL import Image as _Image
        pil = _Image.fromarray(arr)
        new_w = max(1, round(pil.width * cfg.scale))
        new_h = max(1, round(pil.height * cfg.scale))
        pil = pil.resize((new_w, new_h), _Image.LANCZOS)
        arr = np.array(pil)
        if alpha_mask is not None:
            mask_pil = _Image.fromarray((alpha_mask.astype(np.uint8) * 255))
            mask_pil = mask_pil.resize((new_w, new_h), _Image.NEAREST)
            alpha_mask = np.array(mask_pil) > 128

    return arr, alpha_mask


def preprocess_image(path: str, cfg: Optional[PreprocessConfig] = None) -> LoadResult:
    """便捷入口：读取图片并应用预处理。"""
    result = load_image(path)
    if cfg is None:
        cfg = PreprocessConfig()
    arr, mask = apply_preprocess(to_array(result.image), result.alpha_mask, cfg)
    result.image = Image.fromarray(arr)
    result.alpha_mask = mask
    result.width = arr.shape[1]
    result.height = arr.shape[0]
    return result


if __name__ == "__main__":
    print("input module ok")
