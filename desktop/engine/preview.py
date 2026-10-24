"""
离线方块预览渲染。

用途：不连接游戏、不发送任何指令，仅把蓝图或匹配网格渲染成一张
「方块化预览图」，供外部程序 UI 调试与用户确认效果。

同源约束（需求 I.4）：本模块只负责把 block_id 上色，**不做任何匹配或颜色
换算**；颜色一律取自 blocks.build_block_library（默认贴图固有色色库），
与引擎生成蓝图时用的是同一套色库。需要 A/B 对比时显式传 library。

纯本地计算，不依赖 tModLoader。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image

from .blocks import build_block_library

# 未知方块的回退颜色
FALLBACK_RGB = (90, 90, 90)


def color_map(library: Optional[dict] = None) -> Dict[str, Tuple[int, int, int]]:
    """block_id -> 主色 RGB；library 缺省时取 blocks 的默认色库（贴图固有色）。"""
    lib = library if library is not None else build_block_library()
    return {bid: tuple(int(c) for c in bc.main_rgb) for bid, bc in lib.items()}


# 旧名字保留（内部/外部都可能引用）
def _color_map(library: Optional[dict] = None) -> Dict[str, Tuple[int, int, int]]:
    return color_map(library)


def _rgba_from_grid(grid: np.ndarray, colors: Dict[str, Tuple[int, int, int]]) -> np.ndarray:
    """(H, W) block_id -> (H, W, 4) RGBA；空字符串/None 透明，未知方块用回退色。"""
    arr = np.asarray(grid, dtype=object)
    h, w = arr.shape
    flat = arr.ravel()
    rgba = np.zeros((flat.size, 4), dtype=np.uint8)
    valid = np.fromiter((bool(v) for v in flat), dtype=bool, count=flat.size)
    for block_id, rgb in colors.items():
        hit = flat == block_id
        if hit.any():
            rgba[hit] = (rgb[0], rgb[1], rgb[2], 255)
    unknown = valid & (rgba[:, 3] == 0)
    if unknown.any():
        rgba[unknown] = (FALLBACK_RGB[0], FALLBACK_RGB[1], FALLBACK_RGB[2], 255)
    return rgba.reshape(h, w, 4)


def render_grid(
    matched_grid: np.ndarray,
    scale: int = 1,
    library: Optional[dict] = None,
) -> Image.Image:
    """把匹配网格 (H, W) 渲染成预览图；空字符串表示透明/跳过。"""
    colors = color_map(library)
    rgba = _rgba_from_grid(np.asarray(matched_grid, dtype=object), colors)
    img = Image.fromarray(rgba, "RGBA")
    h, w = rgba.shape[:2]
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    return img


def render_blueprint(
    blueprint,
    scale: int = 1,
    library: Optional[dict] = None,
) -> Image.Image:
    """把蓝图渲染成预览图（按 chunk.offset + block 相对坐标还原位置）。"""
    def _v(obj, name, default=None):
        return getattr(obj, name) if hasattr(obj, name) else (obj.get(name, default) if isinstance(obj, dict) else default)

    w = int(_v(blueprint, "width", 0) or 0)
    h = int(_v(blueprint, "height", 0) or 0)
    chunks = _v(blueprint, "chunks", []) or []

    if w <= 0 or h <= 0:
        # 尺寸缺失时按分块推算
        max_x = max_y = 0
        for ch in chunks:
            max_x = max(max_x, int(_v(ch, "offset_x", 0)) + int(_v(ch, "width", 0)))
            max_y = max(max_y, int(_v(ch, "offset_y", 0)) + int(_v(ch, "height", 0)))
        w, h = max_x, max_y

    if w <= 0 or h <= 0:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    colors = color_map(library)
    canvas = np.zeros((h, w, 4), dtype=np.uint8)

    for ch in chunks:
        ox = int(_v(ch, "offset_x", 0))
        oy = int(_v(ch, "offset_y", 0))
        blocks = _v(ch, "blocks", []) or []
        if not blocks:
            continue
        xs = np.empty(len(blocks), dtype=np.int64)
        ys = np.empty(len(blocks), dtype=np.int64)
        ids = np.empty(len(blocks), dtype=object)
        for i, b in enumerate(blocks):
            xs[i] = ox + int(_v(b, "x", 0))
            ys[i] = oy + int(_v(b, "y", 0))
            ids[i] = _v(b, "block_id", "") or ""

        # 按 id 分组上色（每个分块内 id 种类很少），避免逐像素 Python 写图
        uniq = set(ids.tolist())
        for block_id in uniq:
            if not block_id:
                continue
            rgb = colors.get(block_id, FALLBACK_RGB)
            sel = ids == block_id
            bx, by = xs[sel], ys[sel]
            keep = (bx >= 0) & (bx < w) & (by >= 0) & (by < h)
            canvas[by[keep], bx[keep]] = (rgb[0], rgb[1], rgb[2], 255)

    img = Image.fromarray(canvas, "RGBA")
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    return img


def blueprint_statistics(blueprint) -> dict:
    """统计蓝图方块：总数、种类数、每种数量（按数量降序）。"""
    def _v(obj, name, default=None):
        return getattr(obj, name) if hasattr(obj, name) else (obj.get(name, default) if isinstance(obj, dict) else default)

    counts: Dict[str, int] = {}
    total = 0
    for ch in (_v(blueprint, "chunks", []) or []):
        for b in (_v(ch, "blocks", []) or []):
            bid = _v(b, "block_id", "")
            if not bid:
                continue
            counts[bid] = counts.get(bid, 0) + 1
            total += 1

    ordered = dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
    return {
        "total_blocks": total,
        "unique_blocks": len(ordered),
        "by_block": ordered,
        "chunks": len(_v(blueprint, "chunks", []) or []),
    }
