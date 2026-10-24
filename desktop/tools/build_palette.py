"""从导出后的原版贴图采样无光照基准固有色，生成 engine/palette_auto.py。

贴图布局：288x270 = 16 列 x 15 行 的 18px 单元格（16px 精灵在格左上，右侧与下侧各 2px
透明 padding）。实测该偏移是唯一能让单元格 8 个边界点全不透明的对齐方式。

三道过滤：
1. 实心整块帧：16x16 精灵的 4 角 + 4 条边中点全部 alpha=255 且非纯黑。斜坡、半砖、
   接缝帧被筛掉。
2. 混入帧剔除：Terraria 的方块片里混有「石块被泥土包围」这类过渡帧——帧内同时存在
   中性灰石块区与暖棕异材质区（边界环色度跳变，实测 R-B 从 0 跳到 33）。判据取
   「边框环 vs 中心核的 Lab 色度差 >= RING_DC_MAX」，只丢边缘换材质的帧，不动木纹/
   砂浆这类同色相纹理。剩不足 MIN_CLEAN 帧则整片回退，不静默取子集。
3. 估计量：逐帧取 16x16 精灵的像素均值，再对帧集合逐通道取中位数（对残余离群帧不敏感）。

solid_rgb 用上述口径；solid_rgb_band 保留旧的「亮带均值」用于 A/B，管线不读它。

用法：
    python tools/build_palette.py                       # 写到 engine/palette_auto.py
    python tools/build_palette.py --out /tmp/p.py       # 自定义输出路径
    python tools/build_palette.py --tiles DIR --report  # 换贴图目录并打印对照表
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.blocks import BLOCKS, BLOCK_NAME_TO_TILE_ID  # noqa: E402
from engine.matcher import rgb_to_lab_array  # noqa: E402

DEFAULT_TILES = ROOT / "assets" / "tiles"
DEFAULT_OUT = ROOT / "engine" / "palette_auto.py"
PURE_SCALE = 6.0        # variance 每增加 6，pure_score 减半左右
ALPHA_MIN = 128
L_TRIM = 5.0            # 全片口径：L* 分位裁剪界（百分点）
CELL = 18               # 单元格步长
SPRITE = 16             # 精灵边长
RING = 2                # 边框环宽度（像素）
EDGE = 1                # 逐帧均值只取 16x16 精灵内部，剔除自带 1px 描边
CORE = 4                # 中心核 8x8：距边 4px 起（与独立实现对齐）
RING_DC_MAX = 5.0       # 边框环与中心核的 Lab 色度差上限，超过即判为混入异材质
MIN_CLEAN = 3           # clean 帧少于此数则整片回退
BAND_LO = 70.0          # solid_rgb_band 口径：L* 最亮 30%
SAMPLE_BUCKETS = 4
_BUCKET = 16
_SAMPLE_QUANTILES = (2.0, 34.0, 66.0, 98.0)

_TEMPLATE = '''"""自动生成：Terraria 原版贴图采样出的无光照基准固有色 + 纹理方差。

由 tools/build_palette.py 生成，请勿手工编辑。
数据来源：原版贴图 Tiles_<tile_id>.xnb 解码后的 PNG（无光照干扰，纯像素统计）。

口径：
- solid_rgb：实心整块帧（4 角 + 4 边中点全不透明）逐帧取 16x16 精灵均值，
  剔除「边缘换材质」的混入帧后逐通道取中位数。这是匹配应使用的固有色。
- solid_variance / solid_pure_score：同一批帧内部的 Lab 距离标准差与其归一化纯色度。
- solid_rgb_band：同一批帧的「L* 最亮 30% 均值」，仅用于 A/B 对照，管线不读。
- avg_rgb / variance / pure_score：整张片口径，保留仅为向后兼容。
- frames 为识别到的实心帧总数，frames_clean 为剔除混入帧后实际参与统计的帧数。
- samples：实心干净帧内沿 L* 的 4 个代表点（最暗 / 中间偏暗 / 中间偏亮 / 最亮）。

字段：tile_id / solid_rgb / solid_variance / solid_pure_score / solid_rgb_band /
      frames / frames_clean / avg_rgb / samples / variance / pure_score。
"""

from __future__ import annotations

import numpy as np

PALETTE: dict[str, dict] = {
@@ENTRIES@@
}

_BLOCK_IDS: tuple = (@@BLOCK_IDS@@)


def get_palette() -> dict:
    """返回 block_id -> 色卡条目的映射。"""
    return PALETTE


def get_entry(block_id: str) -> dict | None:
    """按 block_id 取单个色卡条目，缺失返回 None。"""
    return PALETTE.get(block_id)


def coverage() -> float:
    """成功覆盖的方块比例（0~1）。"""
    if not _BLOCK_IDS:
        return 0.0
    return sum(1 for bid in _BLOCK_IDS if bid in PALETTE) / len(_BLOCK_IDS)


def base_rgb(block_id: str) -> list | None:
    """匹配用固有色：实心整块口径，缺失返回 None。"""
    entry = PALETTE.get(block_id)
    return entry.get("solid_rgb") if entry else None


def pure_score(block) -> float:
    """纯色度 0~1：接受 block_id / BlockColor 对象 / samples 列表 / 主色。

    优先实心整块口径；matcher.py 会依次用这三种实参试探，任一可用即返回。
    """
    if isinstance(block, str):
        entry = PALETTE.get(block)
        return _entry_purity(entry) if entry else 0.0

    bid = getattr(block, "block_id", None)
    if isinstance(bid, str):
        entry = PALETTE.get(bid)
        if entry:
            return _entry_purity(entry)
        return _score_from_samples(getattr(block, "samples", None))

    if isinstance(block, (list, tuple)):
        if len(block) == 3 and all(isinstance(c, (int, float)) for c in block):
            return 1.0
        return _score_from_samples(block)
    raise TypeError("pure_score 无法识别的实参类型：%r" % (type(block),))


def _entry_purity(entry: dict) -> float:
    if entry.get("solid_rgb") is not None:
        return float(entry["solid_pure_score"])
    return float(entry["pure_score"])


def _score_from_samples(samples) -> float:
    """按采样点在 RGB 立方内的离散度估算纯色度。"""
    pts = [tuple(int(c) for c in s) for s in (samples or []) if len(s) == 3]
    if len(pts) <= 1:
        return 1.0
    arr = np.asarray(pts, dtype=np.float64)
    spread = float(np.sqrt(((arr - arr.mean(axis=0)) ** 2).sum(axis=1)).mean())
    return 1.0 / (1.0 + spread / 24.0)
'''


def _lab(pix: np.ndarray) -> np.ndarray:
    return rgb_to_lab_array(np.asarray(pix, dtype=np.uint8).reshape(-1, 3))


def _spread(labs: np.ndarray) -> float:
    """Lab 距离标准差（相对质心的 Delta E 离散度）。"""
    centroid = labs.mean(axis=0)
    return float(np.sqrt(((labs - centroid) ** 2).sum(axis=1)).std())


def _erode3(mask: np.ndarray) -> np.ndarray:
    """3x3 腐蚀：邻域全 True 才保留（剔除贴图描边）。"""
    padded = np.pad(mask, 1, constant_values=False)
    out = mask.copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out &= padded[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
    return out


def solid_frames(rgba: np.ndarray) -> list:
    """返回全部实心整块帧的 16x16 RGB 精灵数组。"""
    h, w = rgba.shape[:2]
    if h % CELL or w % CELL:
        return []
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3]
    frames = []
    for row in range(h // CELL):
        for col in range(w // CELL):
            y, x = row * CELL, col * CELL
            sp_a = alpha[y:y + SPRITE, x:x + SPRITE]
            if sp_a.shape != (SPRITE, SPRITE):
                continue
            border = (sp_a[0, 0], sp_a[0, SPRITE - 1], sp_a[SPRITE - 1, 0],
                      sp_a[SPRITE - 1, SPRITE - 1], sp_a[0, SPRITE // 2],
                      sp_a[SPRITE - 1, SPRITE // 2], sp_a[SPRITE // 2, 0],
                      sp_a[SPRITE // 2, SPRITE - 1])
            if min(border) != 255:
                continue
            sp = rgb[y:y + SPRITE, x:x + SPRITE]
            if int(sp.max()) <= 8:
                continue
            frames.append(sp)
    return frames


def _ring_core_dc(sp: np.ndarray) -> float:
    """边框环与中心核的 Lab 色度差；混入异材质的帧靠这一项暴露。"""
    yy, xx = np.mgrid[0:SPRITE, 0:SPRITE]
    depth = np.minimum(np.minimum(yy, xx),
                       np.minimum(SPRITE - 1 - yy, SPRITE - 1 - xx))
    ring = sp[depth <= RING].reshape(-1, 3)
    core = sp[depth >= CORE].reshape(-1, 3)
    ring_lab = _lab(ring).mean(axis=0)
    core_lab = _lab(core).mean(axis=0)
    return float(np.hypot(ring_lab[1] - core_lab[1], ring_lab[2] - core_lab[2]))


def clean_frames(frames: list) -> tuple:
    """(参与统计的帧, 被剔除的数量)；帧数过少则整片回退。"""
    if len(frames) <= MIN_CLEAN:
        return frames, 0
    routed = [(_ring_core_dc(sp), sp) for sp in frames]
    keep = [sp for dc, sp in routed if dc < RING_DC_MAX]
    if len(keep) < MIN_CLEAN:
        return frames, 0
    return keep, len(frames) - len(keep)


def mosaic_pixel(sp: np.ndarray) -> np.ndarray:
    """按 1:1 平铺 10x10 后降采样成 1 像素——玩家看一片该方块收到的颜色。"""
    tiled = np.tile(sp, (10, 10, 1))
    return np.array(Image.fromarray(tiled).resize((1, 1), Image.BOX)).reshape(3)


def _base_band(pix: np.ndarray) -> np.ndarray:
    """取 L* 最亮 (100-BAND_LO)% 的像素（仅用于 solid_rgb_band）。"""
    labs = _lab(pix)
    band = pix[labs[:, 0] >= np.percentile(labs[:, 0], BAND_LO)]
    return band if len(band) >= 8 else pix


def _quantile_samples(pix: np.ndarray) -> list:
    """沿 L* 取最暗 / 中间偏暗 / 中间偏亮 / 最亮 四个代表点。"""
    ranked = pix[np.argsort(_lab(pix)[:, 0])]
    out = []
    for q in _SAMPLE_QUANTILES:
        idx = min(len(ranked) - 1, max(0, int(round(q / 100.0 * (len(ranked) - 1)))))
        out.append([int(v) for v in ranked[idx]])
    return out


def _dominant(pix: np.ndarray) -> list:
    """整片兜底：RGB 量化直方图取前 N 个色块的代表色。"""
    keys = (pix // _BUCKET).astype(np.int32)
    flat = keys[:, 0] * 1024 + keys[:, 1] * 32 + keys[:, 2]
    _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
    out = []
    for idx in np.argsort(-counts)[:SAMPLE_BUCKETS]:
        out.append([int(round(v)) for v in pix[inverse == idx].mean(axis=0)])
    return out


def analyze(png_path: Path) -> dict | None:
    """统计一张贴图，返回色卡条目；有效像素不足返回 None。"""
    rgba = np.array(Image.open(png_path).convert("RGBA"))
    rgb = rgba[:, :, :3].astype(np.float64)
    opaque = rgba[:, :, 3] >= ALPHA_MIN
    core = _erode3(opaque)
    if not core.any():
        core = opaque
    if not core.any():
        return None

    sheet_pix = rgb[core]
    sheet_labs = _lab(sheet_pix)
    lo, hi = np.percentile(sheet_labs[:, 0], [L_TRIM, 100.0 - L_TRIM])
    keep = (sheet_labs[:, 0] >= lo) & (sheet_labs[:, 0] <= hi)
    if keep.sum() < 16:
        keep = np.ones(len(sheet_labs), dtype=bool)
    sheet_pix, sheet_labs = sheet_pix[keep], sheet_labs[keep]
    sheet_var = _spread(sheet_labs)

    entry = {
        "avg_rgb": [int(round(v)) for v in sheet_pix.mean(axis=0)],
        "variance": round(sheet_var, 2),
        "pure_score": round(1.0 / (1.0 + sheet_var / PURE_SCALE), 3),
    }

    frames = solid_frames(rgba)
    if not frames:
        entry.update({"solid_rgb": None, "solid_variance": None, "solid_pure_score": 0.0,
                      "solid_rgb_band": None, "frames": 0, "frames_clean": 0,
                      "samples": _dominant(sheet_pix)})
        return entry

    used, dropped = clean_frames(frames)
    inner = [sp[EDGE:SPRITE - EDGE, EDGE:SPRITE - EDGE] for sp in used]
    per_frame = np.array([t.reshape(-1, 3).astype(np.float64).mean(axis=0) for t in inner])
    solid_rgb = np.median(per_frame, axis=0)
    pixels = np.concatenate([t.reshape(-1, 3) for t in inner]).astype(np.float64)
    solid_var = _spread(_lab(pixels))
    entry.update({
        "solid_rgb": [int(round(v)) for v in solid_rgb],
        "solid_variance": round(solid_var, 2),
        "solid_pure_score": round(1.0 / (1.0 + solid_var / PURE_SCALE), 3),
        "solid_rgb_band": [int(round(v)) for v in _base_band(pixels).mean(axis=0)],
        "frames": len(frames),
        "frames_clean": len(used),
        "samples": _quantile_samples(pixels),
    })
    return entry


def consistency(png_path: Path, entry: dict) -> tuple:
    """降采样一致律：固定规则选代表帧——clean 帧按帧内 L* 中位数排名取正中间那帧，
    平铺 10x10 后降采样成 1 像素，与 solid_rgb 比较；同时给出逐帧降采样值相对
    solid_rgb 的极差（帧间真实波动，不是失败）。"""
    rgba = np.array(Image.open(png_path).convert("RGBA"))
    frames = solid_frames(rgba)
    if not frames or entry["solid_rgb"] is None:
        return None, None, None
    used, _ = clean_frames(frames)
    tiles = [sp[EDGE:SPRITE - EDGE, EDGE:SPRITE - EDGE] for sp in used]
    mos = np.array([mosaic_pixel(t) for t in tiles], dtype=np.float64)
    lums = np.array([np.median(_lab(t.reshape(-1, 3))[:, 0]) for t in tiles])
    rep = int(np.argsort(lums)[len(lums) // 2])
    dev = np.abs(mos - np.array(entry["solid_rgb"], dtype=np.float64)).max(axis=1)
    return int(round(dev[rep])), int(round(dev.min())), int(round(dev.max()))

def build(tiles_dir: Path) -> tuple:
    """返回 (palette, missing, failed, no_frames)。"""
    palette, missing, failed, no_frames = {}, [], [], []
    for block in BLOCKS:
        bid = block.block_id
        tile_id = BLOCK_NAME_TO_TILE_ID.get(bid)
        if tile_id is None or tile_id < 0:
            missing.append(bid + "(无 TileID)")
            continue
        png = tiles_dir / ("Tiles_%d.png" % tile_id)
        if not png.exists():
            missing.append("%s(缺 %s)" % (bid, png.name))
            continue
        try:
            entry = analyze(png)
        except Exception as exc:
            failed.append("%s: %s: %s" % (bid, type(exc).__name__, exc))
            continue
        if entry is None:
            failed.append("%s: 有效像素不足" % bid)
            continue
        if entry["frames"] == 0:
            no_frames.append(bid)
        palette[bid] = {"tile_id": tile_id, **entry}
    return palette, missing, failed, no_frames


def render(palette: dict) -> str:
    """把色卡渲染成 palette_auto.py 源码。"""
    lines = []
    for bid, e in palette.items():
        lines.append(
            '    "%s": {"tile_id": %d, "solid_rgb": %s, "solid_variance": %s, '
            '"solid_pure_score": %s, "solid_rgb_band": %s,' % (
                bid, e["tile_id"], e["solid_rgb"], e["solid_variance"],
                e["solid_pure_score"], e["solid_rgb_band"]))
        lines.append(
            '     "frames": %d, "frames_clean": %d, "avg_rgb": %s, "samples": %s, '
            '"variance": %s, "pure_score": %s},' % (
                e["frames"], e["frames_clean"], e["avg_rgb"], e["samples"],
                e["variance"], e["pure_score"]))
    block_ids = ", ".join('"%s"' % b.block_id for b in BLOCKS)
    body = chr(10).join(lines)
    return (_TEMPLATE.replace("@@ENTRIES@@", body)
            .replace("@@BLOCK_IDS@@", block_ids))


REPORT_BLOCKS = ("Stone", "Dirt", "Grass", "Sand", "Sandstone", "HardenedSand",
                 "Brick", "Snow", "Ice", "Ebonstone", "Mud")


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="生成 engine/palette_auto.py")
    ap.add_argument("--tiles", default=str(DEFAULT_TILES), help="PNG 贴图目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出模块路径")
    ap.add_argument("--report", action="store_true", help="打印全部方块对照表")
    args = ap.parse_args(argv)

    tiles = Path(args.tiles)
    palette, missing, failed, no_frames = build(tiles)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(palette), encoding="utf-8")

    total = len(BLOCKS)
    print("覆盖 %d/%d = %.3f -> %s" % (len(palette), total, len(palette) / total, out))
    if missing:
        print("缺失 %d：%s" % (len(missing), ", ".join(missing)))
    if failed:
        print("失败 %d：%s" % (len(failed), "; ".join(failed)))
    if no_frames:
        print("未识别到实心帧 %d：%s（solid_* 为 None，未用 avg_rgb 顶包）"
              % (len(no_frames), ", ".join(no_frames)))

    rows = [b for b in REPORT_BLOCKS if b in palette] or list(palette)
    print("%-13s %-6s %-7s %-17s %-6s %-6s %-9s %-6s" % (
        "block_id", "solid", "clean", "solid_rgb", "L*", "err", "range", "band"))
    bad = []
    for bid in rows:
        e = palette[bid]
        L = _lab(np.asarray([e["solid_rgb"]]))[0][0] if e["solid_rgb"] else 0.0
        err, dmin, dmax = consistency(tiles / ("Tiles_%d.png" % e["tile_id"]), e)
        if err is not None and err > 3:
            bad.append((bid, err))
        print("%-13s %-6d %-7d %-17s %-6.1f %-6s %-9s %s" % (
            bid, e["frames"], e["frames_clean"], e["solid_rgb"], L, err,
            "%s..%s" % (dmin, dmax), e["solid_rgb_band"]))
    print("降采样一致律（固定代表帧，≤3 通过）：", "全部通过" if not bad else "超标 %s" % bad)

    if args.report:
        print("--- 全量（含混入帧剔除量）---")
        for bid, e in palette.items():
            print("%-13s tile=%-4d frames=%-4d clean=%-4d dropped=%-4d solid=%-18s band=%-18s var=%-6s pure=%.3f"
                  % (bid, e["tile_id"], e["frames"], e["frames_clean"],
                     e["frames"] - e["frames_clean"], e["solid_rgb"], e["solid_rgb_band"],
                     e["solid_variance"], e["solid_pure_score"]))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

