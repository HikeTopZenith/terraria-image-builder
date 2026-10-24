
"""
方块匹配算法核心。

色差默认使用 CIEDE2000（见 colorimetry.py），比旧的 CIELAB 欧氏距离更贴合人眼；
旧算法原样保留在 lab_distance 里，作为 delta_algorithm="lab" 的备选匹配模式。
支持三档匹配模式、黑白名单、常用方块优先，以及四个默认关闭的开关：
色相优先（hue_priority）、纯色方块优先（prefer_pure）、
Floyd–Steinberg 误差扩散抖动（dither）、自定义色卡（use_custom_palette）。

自定义色卡约定：[{"rgb": [r, g, b], "block_id": "Stone"}, ...]，条目上限
MAX_CUSTOM_PALETTE = 50；超过上限直接抛 ValueError（不做静默截断，以免用户
在不知情的情况下丢掉色卡尾部条目）。

依赖：numpy（已内置）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .blocks import BlockColor, build_block_library, get_common_block_ids, BASIC_BLOCK_IDS
from .colorimetry import delta_e_2000_array, hue_angle_delta_array


# ---------- CIELAB 空间转换常数（D65 白点） ----------
_Xn, _Yn, _Zn = 95.047, 100.000, 108.883

# 色差算法：ciede2000 为默认，lab 为旧版 CIELAB 欧氏距离（备选匹配模式）
DELTA_ALGORITHMS = ("ciede2000", "lab")

# 自定义色卡条目上限
MAX_CUSTOM_PALETTE = 50

# 色相项彩度参考值：两端彩度低于该值时色相不可靠，色相项按比例衰减，
# 避免近灰像素（如 (130,128,127)）被当成「色相跑偏」而乱换方块。
_HUE_CHROMA_REF = 15.0
# 源侧彩度门：彩度低于 LO 的像素色相无意义（近灰/近白），色相项必须完全关闭，
# 否则「优先保色相」会把亮中性像素从 Snow(180,214,232) 推到更暗的中性方块上
# （实测：骷髅图 S<0.15 的 32 万像素，匹配亮度均值 77.1 → 73.4，暗了 13 L*）。
# 只在彩度高于 HI 时给满权重，中间用平方平滑过渡，避免跳变。
_HUE_CHROMA_SRC_LO = 8.0
_HUE_CHROMA_SRC_HI = 18.0

# 纯色度估算中 samples 离散度的衰减尺度（Lab 距离）
_PURE_SPREAD_SCALE = 10.0

# dither 行内固定点迭代次数：行内那 7/16 份误差依赖左邻量化结果，迭代 3 轮后
# 与逐像素严格实现的方块选择一致率约 80%，同时避免逐像素 Python 循环拖慢 200k 图。
_DITHER_ROW_PASSES = 3


def _f(t: float) -> float:
    delta = 6.0 / 29.0
    if t > delta ** 3:
        return math.pow(t, 1.0 / 3.0)
    return t / (3.0 * delta * delta) + 4.0 / 29.0


def rgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """将 sRGB (0-255) 转换为 CIELAB (L*, a*, b*)。"""
    r, g, b = [c / 255.0 for c in rgb]

    # sRGB -> 线性 RGB
    def lin(c: float) -> float:
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92

    r_l, g_l, b_l = lin(r), lin(g), lin(b)

    # 线性 RGB -> XYZ (D65)，乘 100 使白点映射到 L=100
    x = (r_l * 0.4124564 + g_l * 0.3575761 + b_l * 0.1804375) * 100.0
    y = (r_l * 0.2126729 + g_l * 0.7151522 + b_l * 0.0721750) * 100.0
    z = (r_l * 0.0193339 + g_l * 0.1191920 + b_l * 0.9503041) * 100.0

    fx, fy, fz = _f(x / _Xn), _f(y / _Yn), _f(z / _Zn)

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    bb = 200.0 * (fy - fz)
    return (L, a, bb)


def lab_distance(lab1: Tuple[float, float, float], lab2: Tuple[float, float, float]) -> float:
    """CIELAB 两点间欧氏距离（近似 Delta E）。保留给 delta_algorithm="lab"。"""
    dL = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    return math.sqrt(dL * dL + da * da + db * db)


def rgb_to_lab_array(rgbs: np.ndarray) -> np.ndarray:
    """(N,3) uint8 -> (N,3) float64 CIELAB，向量化实现（供批量匹配用）。"""
    c = np.asarray(rgbs, dtype=np.float64) / 255.0
    lin = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    r, g, b = lin[:, 0], lin[:, 1], lin[:, 2]

    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) * 100.0
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) * 100.0
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) * 100.0

    delta = 6.0 / 29.0
    d3 = delta ** 3

    def _f_arr(t):
        return np.where(t > d3, np.cbrt(t), t / (3.0 * delta * delta) + 4.0 / 29.0)

    fx = _f_arr(x / _Xn)
    fy = _f_arr(y / _Yn)
    fz = _f_arr(z / _Zn)

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    bb = 200.0 * (fy - fz)
    return np.stack([L, a, bb], axis=1)


def _hue_gap(labs_m: np.ndarray, labs_n: np.ndarray) -> np.ndarray:
    """色相项：(m,n) 的 |Δh'| 角度差（度），按两端彩度向外衰减。"""
    gap = hue_angle_delta_array(labs_m, labs_n)
    cm = np.hypot(labs_m[:, 1], labs_m[:, 2])[:, None]
    cn = np.hypot(labs_n[:, 1], labs_n[:, 2])[None, :]
    src_gate = np.clip((cm - _HUE_CHROMA_SRC_LO) / (_HUE_CHROMA_SRC_HI - _HUE_CHROMA_SRC_LO), 0.0, 1.0) ** 2
    return gap * np.minimum(1.0, np.sqrt(cm * cn) / _HUE_CHROMA_REF) * src_gate


# ---------- 纯色块优先 ----------
# palette_auto.pure_score 由另一模块提供，此刻可能尚未落地：缺失时用 samples
# 的 Lab 离散度就地估算，保证本模块不依赖它先完成。
_pure_score_cache: list = []


def _pure_score_fn():
    """返回 palette_auto.pure_score（可用时），否则 None；每次进程只探测一次。"""
    if not _pure_score_cache:
        try:
            from .palette_auto import pure_score  # type: ignore
        except Exception:
            pure_score = None
        _pure_score_cache.append(pure_score)
    return _pure_score_cache[0]


def _spread_purity(samples: list) -> float:
    """用 samples 的 Lab 离散度估算纯色度（0-1，越集中越接近 1）。"""
    pts = [tuple(int(c) for c in s) for s in (samples or [])]
    if len(pts) <= 1:
        return 1.0
    labs = np.array([rgb_to_lab(p) for p in pts], dtype=np.float64)
    spread = float(np.sqrt(((labs - labs.mean(axis=0)) ** 2).sum(axis=1)).mean())
    return 1.0 / (1.0 + spread / _PURE_SPREAD_SCALE)


def _block_purity(bc: BlockColor) -> float:
    """方块纯色度 0-1：优先 palette_auto.pure_score，缺失时退回 samples 离散度。"""
    fn = _pure_score_fn()
    if fn is not None:
        # pure_score 的签名由另一模块决定，依次尝试方块对象 / 采样点 / 主色
        for arg in (bc, list(bc.samples or []), tuple(bc.main_rgb)):
            try:
                return float(min(1.0, max(0.0, float(fn(arg)))))
            except Exception:
                continue
    return _spread_purity(bc.samples)


def validate_custom_palette(entries) -> List[tuple]:
    """校验并归一化自定义色卡，返回 [((r,g,b), block_id), ...]；非法即抛 ValueError。"""
    items = list(entries or [])
    if len(items) > MAX_CUSTOM_PALETTE:
        raise ValueError(
            f"自定义色卡最多 {MAX_CUSTOM_PALETTE} 条，当前 {len(items)} 条；请先精简色卡"
        )
    out: List[tuple] = []
    for i, e in enumerate(items):
        try:
            rgb = tuple(int(c) for c in e["rgb"])
            bid = str(e["block_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"自定义色卡第 {i + 1} 条格式错误，需要 {{\"rgb\": [r, g, b], \"block_id\": \"Stone\"}}"
            )
        if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
            raise ValueError(f"自定义色卡第 {i + 1} 条 rgb 非法：{rgb}")
        if not bid:
            raise ValueError(f"自定义色卡第 {i + 1} 条 block_id 为空")
        out.append((rgb, bid))
    return out


# ---------- 匹配结果 ----------
@dataclass
class MatchResult:
    block_id: str
    distance: float  # 色差，越小越接近
    mode: str
    source_pixel: Tuple[int, int, int]  # 原始 RGB
    matched_rgb: Tuple[int, int, int]  # 匹配到的方块主色


@dataclass
class MatchConfig:
    mode: str = "precise"  # "precise" | "natural" | "minimal"
    tolerance: float = 40.0  # 纯色模式容差（Delta E 阈值）
    texture_weight: float = 0.4  # 自然过渡模式中纹理采样点权重
    common_priority: bool = True  # 常用方块优先开关
    common_bonus: float = 2.0  # 常用方块距离扣除（越小越优先）
    blacklist: set = field(default_factory=set)  # 禁用的 block_id
    whitelist: Optional[set] = None  # 限定可用范围（None 表示不限）
    minimal_block_ids: set = field(default_factory=lambda: set(BASIC_BLOCK_IDS))  # 极简模式子集
    # 是否允许重力方块（沙子等）参与匹配；默认 False，避免放置后塌方
    allow_falling: bool = False
    # 是否允许半透明方块（玻璃/冰块）参与匹配；默认 False，避免透出黑背景
    allow_transparent: bool = False

    # ---- 以下开关全部默认关闭，全关时行为与旧版一致 ----
    delta_algorithm: str = "ciede2000"  # "ciede2000" | "lab"
    hue_priority: bool = False  # 优先保色相
    hue_weight: float = 0.2  # 色相项权重：0.2 × |Δh'|（度）叠加到色差上
    prefer_pure: bool = False  # 纯色方块优先
    pure_bonus: float = 3.0  # 纯色方块最大距离折扣
    dither: bool = False  # Floyd–Steinberg 误差扩散（需二维入口，且只比主色，见 match_image）
    use_custom_palette: bool = False  # 自定义色卡优先
    custom_palette: list = field(default_factory=list)  # [{"rgb": [r,g,b], "block_id": "Stone"}]
    custom_threshold: float = 12.0  # 色卡命中阈值（Lab 欧氏距离）

    def __post_init__(self) -> None:
        if self.delta_algorithm not in DELTA_ALGORITHMS:
            raise ValueError(
                f"delta_algorithm 只能是 {DELTA_ALGORITHMS}，收到 {self.delta_algorithm!r}"
            )
        # 提前校验色卡，避免非法配置拖到匹配阶段才炸
        if self.custom_palette:
            validate_custom_palette(self.custom_palette)


class _Candidates:
    """一次匹配用的候选方块快照：避免逐像素重算候选集与索引数组。"""

    __slots__ = ("ids", "names", "main_labs", "is_common", "purity", "sample_flat", "sample_groups")

    def __init__(self, matcher: "BlockMatcher", ids: List[str]):
        self.ids = list(ids)
        n = len(self.ids)
        self.names = np.array(self.ids, dtype=object)
        self.main_labs = np.array([matcher._lab_cache[b] for b in self.ids], dtype=np.float64).reshape(n, 3)
        self.is_common = np.array([bool(getattr(matcher.lib[b], "common", False)) for b in self.ids], dtype=bool)
        self.purity = np.array([matcher._purity_of(b) for b in self.ids], dtype=np.float64)

        # 全部纹理采样点拼成 (S,3)，每组区间记在 sample_groups 里：
        # natural 模式因此只需一次色差矩阵运算，而不是每个候选各算一次
        flats: List[np.ndarray] = []
        groups: List[tuple] = []
        pos = 0
        for ci, b in enumerate(self.ids):
            arr = matcher._sample_labs[b]
            if not arr:
                continue
            block = np.asarray(arr, dtype=np.float64).reshape(-1, 3)
            flats.append(block)
            groups.append((ci, pos, pos + len(block)))
            pos += len(block)
        self.sample_flat = np.concatenate(flats) if flats else None
        self.sample_groups = groups


class BlockMatcher:
    """方块匹配器。"""

    def __init__(self, lib: Optional[Dict[str, BlockColor]] = None, config: Optional[MatchConfig] = None):
        self.lib: Dict[str, BlockColor] = lib or build_block_library()
        self.config: MatchConfig = config or MatchConfig()
        # 预计算每个方块主色在 Lab 空间的值
        self._lab_cache: Dict[str, Tuple[float, float, float]] = {}
        self._sample_labs: Dict[str, list] = {}
        for bid, bc in self.lib.items():
            self._lab_cache[bid] = rgb_to_lab(bc.main_rgb)
            self._sample_labs[bid] = [rgb_to_lab(tuple(s)) for s in bc.samples]
        self._purity_cache: Dict[str, float] = {}
        self._custom_sig: Optional[tuple] = None
        self._custom_labs: Optional[np.ndarray] = None
        self._custom_ids: List[str] = []

    def _purity_of(self, block_id: str) -> float:
        v = self._purity_cache.get(block_id)
        if v is None:
            v = _block_purity(self.lib[block_id])
            self._purity_cache[block_id] = v
        return v

    def _is_allowed(self, block_id: str) -> bool:
        if block_id in self.config.blacklist:
            return False
        # 半透明方块默认不参与匹配（否则会透出黑色背景，看起来像阴影）；
        # allow_transparent=True 或白名单显式点名时才启用
        bc = self.lib.get(block_id)
        if bc is not None and getattr(bc, "transparent", False):
            if not getattr(self.config, "allow_transparent", False):
                if self.config.whitelist is None or block_id not in self.config.whitelist:
                    return False
        # 重力方块默认不参与匹配（放置后会下落，破坏建筑结构）
        if bc is not None and getattr(bc, "falling", False):
            if not getattr(self.config, "allow_falling", False):
                if self.config.whitelist is None or block_id not in self.config.whitelist:
                    return False
        if self.config.whitelist is not None and block_id not in self.config.whitelist:
            return False
        if self.config.mode == "minimal" and block_id not in self.config.minimal_block_ids:
            return False
        return True

    def _effective_candidates(self) -> list:
        """返回当前模式下允许的方块 ID 列表（按 lib 顺序）。"""
        return [bid for bid in self.lib.keys() if self._is_allowed(bid)]

    def _candidates(self) -> _Candidates:
        return _Candidates(self, self._effective_candidates())

    def _custom_compiled(self):
        """自定义色卡 -> (labs (P,3), ids, allowed (P,), 方块本色 labs (P,3))；内容不变时复用缓存。"""
        if not self.config.use_custom_palette:
            return None
        entries = validate_custom_palette(self.config.custom_palette)
        if not entries:
            return None
        sig = tuple(entries)
        if sig != self._custom_sig:
            self._custom_labs = np.array([rgb_to_lab(e[0]) for e in entries], dtype=np.float64).reshape(-1, 3)
            self._custom_ids = [e[1] for e in entries]
            # 抖动路径的误差基准是「映射方块的本色」，不是色卡条目指定色；
            # 未知方块填 (0,0,0) 占位，其 allowed 恒为 False，不会被取到
            self._custom_block_labs = np.array(
                [self._lab_cache.get(e[1], (0.0, 0.0, 0.0)) for e in entries], dtype=np.float64
            ).reshape(-1, 3)
            self._custom_sig = sig
        # 命中后若目标方块被黑白名单 / 半透明 / 重力 / 极简子集排除，或方块不存在，
        # 则该条目视为未命中，回退自动匹配
        allowed = np.array(
            [bid in self.lib and self._is_allowed(bid) for bid in self._custom_ids], dtype=bool
        )
        return self._custom_labs, self._custom_ids, allowed, self._custom_block_labs

    def _chunk(self, chunk: int) -> int:
        """CIEDE2000 的中间量多，块放小反而更快（2000 行约 1.5 秒 / 20 万像素）。"""
        if self.config.delta_algorithm == "lab":
            return max(1, int(chunk))
        return max(1, min(int(chunk), 2000))

    def _score(self, labs: np.ndarray, cs: _Candidates, texture: bool = True) -> np.ndarray:
        """(m,3) Lab -> (m,C) 距离矩阵：色差 + 纹理加权 + 色相项 + 纯色/常用折扣。

        texture=False 时只比主色，dither 路径用它（原因见 match_image_dithered）。
        """
        cfg = self.config
        use_lab = cfg.delta_algorithm == "lab"
        if use_lab:
            d = np.sqrt(((labs[:, None, :] - cs.main_labs[None, :, :]) ** 2).sum(axis=2))
        else:
            d = delta_e_2000_array(labs, cs.main_labs)

        tex_w = float(cfg.texture_weight)
        if texture and cfg.mode == "natural" and tex_w > 0.0 and cs.sample_flat is not None:
            # 主色 + 最近纹理采样点加权：按候选逐个算采样点距离（小数组更省内存带宽）
            for ci, start, end in cs.sample_groups:
                block = cs.sample_flat[start:end]
                if use_lab:
                    ds = np.sqrt(((labs[:, None, :] - block[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
                else:
                    ds = delta_e_2000_array(labs, block).min(axis=1)
                d[:, ci] = (1.0 - tex_w) * d[:, ci] + tex_w * ds

        if cfg.hue_priority and cfg.hue_weight:
            d = d + float(cfg.hue_weight) * _hue_gap(labs, cs.main_labs)

        if cfg.prefer_pure:
            # 低纹理纯色方块打折，机制同 common_bonus
            d = np.maximum(0.0, d - float(cfg.pure_bonus) * cs.purity[None, :])

        if cfg.common_priority and cs.is_common.any():
            d[:, cs.is_common] = np.maximum(0.0, d[:, cs.is_common] - float(cfg.common_bonus))
        return d

    def _nearest(self, labs: np.ndarray, cs: _Candidates, chunk: int) -> Tuple[np.ndarray, np.ndarray]:
        """(m,3) Lab -> (best_idx (m,), best_dist (m,))，按块计算控制内存。"""
        best = np.empty(len(labs), dtype=np.int64)
        dist = np.empty(len(labs), dtype=np.float64)
        for start in range(0, len(labs), chunk):
            end = min(len(labs), start + chunk)
            d = self._score(labs[start:end], cs)
            idx = d.argmin(axis=1)
            best[start:end] = idx
            dist[start:end] = d[np.arange(end - start), idx]
        return best, dist

    def _match_core(self, labs: np.ndarray, cs: _Candidates, chunk: int):
        """(m,3) Lab -> (ids (m,) object 或 None, dists (m,))：色卡优先，未命中走自动匹配。"""
        n = len(labs)
        ids = np.empty(n, dtype=object)
        ids[:] = None
        dists = np.full(n, np.inf)
        pending = np.ones(n, dtype=bool)

        palette = self._custom_compiled()
        if palette is not None:
            p_labs, p_ids, p_allowed, _ = palette
            # 色卡命中：Lab 欧氏距离（色卡是精确指定，不随 delta_algorithm 变化）
            pd = np.sqrt(((labs[:, None, :] - p_labs[None, :, :]) ** 2).sum(axis=2))
            pj = pd.argmin(axis=1)
            pbest = pd[np.arange(n), pj]
            hit = (pbest <= float(self.config.custom_threshold)) & p_allowed[pj]
            if hit.any():
                ids[hit] = np.asarray(p_ids, dtype=object)[pj[hit]]
                dists[hit] = pbest[hit]
                pending &= ~hit

        if pending.any() and cs.ids:
            sub = np.nonzero(pending)[0]
            idx, dd = self._nearest(labs[sub], cs, chunk)
            ids[sub] = cs.names[idx]
            dists[sub] = dd
        return ids, dists

    def match_pixel(self, rgb: Tuple[int, int, int]) -> Optional[MatchResult]:
        """匹配单个像素到一个方块。返回 None 表示没有可用方块。

        与 match_rgb_batch 走同一套判定（同一份 _score/_match_core），不再有差异。
        """
        lab = rgb_to_lab_array(np.asarray([rgb], dtype=np.float64))
        cs = self._candidates()
        ids, dists = self._match_core(lab, cs, self._chunk(50000))
        bid = ids[0]
        if bid is None:
            return None
        return MatchResult(
            block_id=bid,
            distance=float(dists[0]),
            mode=self.config.mode,
            source_pixel=rgb,
            matched_rgb=self.lib[bid].main_rgb,
        )

    def match_rgb_batch(self, rgbs: np.ndarray, chunk: int = 50000) -> np.ndarray:
        """向量化批量匹配：(N,3) uint8 -> (N,) object，元素为 block_id 或 None。

        与 match_pixel 完全同源（主色 / 纹理加权 / 色相 / 纯色 / 常用优先 / 色卡），
        整批用 numpy 计算，CIEDE2000 也是矩阵化的，不做逐像素 Python 循环。
        """
        rgbs = np.asarray(rgbs, dtype=np.uint8).reshape(-1, 3)
        n = rgbs.shape[0]
        out = np.empty(n, dtype=object)
        out[:] = None
        if n == 0:
            return out
        cs = self._candidates()
        palette = self._custom_compiled()
        if not cs.ids and palette is None:
            return out
        ids, _ = self._match_core(rgb_to_lab_array(rgbs), cs, self._chunk(chunk))
        return ids

    def match_image(self, pixels: np.ndarray, progress_cb=None, alpha_mask: Optional[np.ndarray] = None) -> np.ndarray:
        """匹配整张图片：(H, W, 3) uint8 -> (H, W) object（block_id 或 ""）。

        pixels 应为已预处理过的 RGB 数组（RGBA 请先取前三个通道）。
        dither 开启时自动改走 Floyd–Steinberg 误差扩散（match_image_dithered）。
        """
        if self.config.dither:
            return self.match_image_dithered(pixels, alpha_mask, progress_cb)

        pixels = np.asarray(pixels, dtype=np.uint8)
        h, w, _ = pixels.shape
        result = np.empty((h, w), dtype=object)
        total = h * w
        cs = self._candidates()
        rows = max(1, 20000 // max(1, w))
        done = 0
        for y0 in range(0, h, rows):
            y1 = min(h, y0 + rows)
            ids, _ = self._match_core(
                rgb_to_lab_array(pixels[y0:y1].reshape(-1, 3)), cs, self._chunk(50000)
            )
            result[y0:y1] = ids.reshape(-1, w)
            done += (y1 - y0) * w
            if progress_cb:
                progress_cb(done, total)
        result[result == None] = ""  # noqa: E711 无可选方块时与逐像素入口保持一致
        return result

    def _quantize_row(self, labs_row: np.ndarray, cs: _Candidates, palette):
        """整行量化 -> (block_id 或 None (w,), 误差基准 Lab (w,3))。

        只比主色（texture=False）且误差基准取方块本色：调色板每个条目只能有一个
        可表示颜色，否则误差扩散没有可比的目标（见 match_image_dithered）。
        """
        w = len(labs_row)
        ids = np.empty(w, dtype=object)
        ids[:] = None
        block_labs = np.zeros((w, 3))
        pending = np.ones(w, dtype=bool)

        if palette is not None:
            p_labs, p_ids, p_allowed, p_block_labs = palette
            pd = np.sqrt(((labs_row[:, None, :] - p_labs[None, :, :]) ** 2).sum(axis=2))
            pj = pd.argmin(axis=1)
            pbest = pd[np.arange(w), pj]
            hit = (pbest <= float(self.config.custom_threshold)) & p_allowed[pj]
            if hit.any():
                ids[hit] = np.asarray(p_ids, dtype=object)[pj[hit]]
                block_labs[hit] = p_block_labs[pj[hit]]
                pending &= ~hit

        if cs.ids and pending.any():
            idx = self._score(labs_row, cs, texture=False).argmin(axis=1)
            ids[pending] = cs.names[idx[pending]]
            block_labs[pending] = cs.main_labs[idx[pending]]
        return ids, block_labs

    def match_image_dithered(
        self,
        pixels: np.ndarray,
        alpha_mask: Optional[np.ndarray] = None,
        progress_cb=None,
    ) -> np.ndarray:
        """Floyd–Steinberg 误差扩散匹配：(H, W, 3) uint8 -> (H, W) object（block_id 或 ""）。

        误差在 Lab 空间按 7/16 右、3/16 左下、5/16 下、1/16 右下扩散。整行一次性
        量化（numpy 行向量化，无逐像素循环），行内那 7/16 份用 _DITHER_ROW_PASSES
        次固定点迭代吸收；行间那三份权重严格按 FS 传播。要位级复刻逐像素串行 FS
        就得到 18 秒 / 20 万像素，与 10 秒预算冲突，故取行内定点迭代。

        抖动路径的量化口径固定为「方块本色 main_rgb」：选块只比主色（不做
        natural 的纹理加权），误差也用方块本色的 Lab 相减。因为 Floyd–Steinberg
        的前提是调色板里每个条目只有一个可表示颜色——natural 用纹理采样点给同一
        方块多个代表色，会让残差无处可比，还会把纹理噪声当成颜色误差扩散出去。
        色卡命中优先不变，命中项同样取其所映射方块的本色做误差基准。
        因此抖动结果与 mode 无关（precise / natural 输出完全一致）。
        alpha_mask 为 False 的像素（透明区域）既不匹配也不扩散误差。
        同一输入必得同一输出（无随机数、无字典序遍历）。
        """
        pixels = np.asarray(pixels, dtype=np.uint8)
        if pixels.ndim != 3 or pixels.shape[2] < 3:
            raise ValueError("dither 需要 (H, W, 3) 的 RGB 数组")
        pixels = pixels[..., :3]
        h, w, _ = pixels.shape
        result = np.empty((h, w), dtype=object)
        result[:] = ""

        cs = self._candidates()
        palette = self._custom_compiled()
        if not cs.ids and palette is None:
            return result

        mask = np.ones((h, w), dtype=bool) if alpha_mask is None else np.asarray(alpha_mask, dtype=bool)
        labs = rgb_to_lab_array(pixels.reshape(-1, 3)).reshape(h, w, 3)
        lo = np.array([0.0, -160.0, -160.0])
        hi = np.array([100.0, 160.0, 160.0])

        cur = np.zeros((w, 3))  # 本行已累积的误差（来自上一行）
        for y in range(h):
            row_labs = labs[y]
            keep = mask[y]
            target = np.clip(row_labs + cur, lo, hi)
            for _ in range(_DITHER_ROW_PASSES):
                ids, block_labs = self._quantize_row(target, cs, palette)
                err = target - block_labs
                err[~keep] = 0.0
                if _ + 1 < _DITHER_ROW_PASSES:
                    # 行内 7/16：用本轮量化结果修正后再迭代一次
                    intra = np.zeros_like(err)
                    intra[1:] = err[:-1] * (7.0 / 16.0)
                    target = np.clip(row_labs + cur + intra, lo, hi)

            ids[~keep] = ""
            ids[ids == None] = ""  # noqa: E711 行内没有可用方块时按空像素处理
            result[y] = ids

            # 3/16 左下、5/16 下、1/16 右下
            nxt = np.zeros((w, 3))
            nxt[:-1] += err[1:] * (3.0 / 16.0)
            nxt += err * (5.0 / 16.0)
            nxt[1:] += err[:-1] * (1.0 / 16.0)
            cur = nxt
            if progress_cb:
                progress_cb((y + 1) * w, h * w)
        return result


def demo():
    lib = build_block_library()
    m = BlockMatcher(lib)
    # 测试几个典型颜色
    for name, rgb in [("泥土棕", (139, 111, 75)), ("天蓝", (100, 150, 255)), ("草绿", (80, 150, 60))]:
        res = m.match_pixel(rgb)
        print(f"{name} {rgb} -> {res.block_id} (dist={res.distance:.2f})")

    # 自然过渡模式
    m2 = BlockMatcher(lib, MatchConfig(mode="natural"))
    res = m2.match_pixel((140, 120, 80))
    print(f"natural (140,120,80) -> {res.block_id} (dist={res.distance:.2f})")

    # 极简模式
    m3 = BlockMatcher(lib, MatchConfig(mode="minimal"))
    res = m3.match_pixel((140, 120, 80))
    print(f"minimal (140,120,80) -> {res.block_id} (dist={res.distance:.2f})")


if __name__ == "__main__":
    demo()
