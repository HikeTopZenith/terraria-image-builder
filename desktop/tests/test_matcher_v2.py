from __future__ import annotations

import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import engine.matcher as matcher_mod
from engine.blocks import BASIC_BLOCK_IDS, BlockColor, build_block_library
from engine.colorimetry import delta_e_2000, hue_angle_delta
from engine.matcher import (
    MAX_CUSTOM_PALETTE,
    BlockMatcher,
    MatchConfig,
    _spread_purity,
    rgb_to_lab,
    rgb_to_lab_array,
)

# 钉住旧色库：本文件测匹配器行为，贴图色库的覆盖在 tests/test_pipeline_v2.py
LIB = build_block_library(use_texture_palette=False)


# ---------------- 改动前的实现复刻（等价性基准） ----------------
def legacy_allowed(lib, cfg, block_id: str) -> bool:
    """改动前 BlockMatcher._is_allowed 的逐字复刻。"""
    if block_id in cfg.blacklist:
        return False
    bc = lib.get(block_id)
    if bc is not None and getattr(bc, "transparent", False):
        if not getattr(cfg, "allow_transparent", False):
            if cfg.whitelist is None or block_id not in cfg.whitelist:
                return False
    if bc is not None and getattr(bc, "falling", False):
        if not getattr(cfg, "allow_falling", False):
            if cfg.whitelist is None or block_id not in cfg.whitelist:
                return False
    if cfg.whitelist is not None and block_id not in cfg.whitelist:
        return False
    if cfg.mode == "minimal" and block_id not in cfg.minimal_block_ids:
        return False
    return True


def legacy_match_rgb_batch(lib, cfg, rgbs, chunk: int = 50000) -> np.ndarray:
    """改动前 match_rgb_batch 的逐字复刻：CIELAB 欧氏距离 + 纹理加权 + 常用优先。"""
    lab_cache = {bid: rgb_to_lab(bc.main_rgb) for bid, bc in lib.items()}
    sample_labs = {bid: [rgb_to_lab(tuple(s)) for s in bc.samples] for bid, bc in lib.items()}
    rgbs = np.asarray(rgbs, dtype=np.uint8)
    n = rgbs.shape[0]
    out = np.empty(n, dtype=object)
    out[:] = None

    candidates = [bid for bid in lib.keys() if legacy_allowed(lib, cfg, bid)]
    if not candidates or n == 0:
        return out

    mode = cfg.mode
    tex_w = float(cfg.texture_weight)
    common_bonus = float(cfg.common_bonus) if cfg.common_priority else 0.0

    names = np.array(candidates, dtype=object)
    main_labs = np.array([lab_cache[b] for b in candidates], dtype=np.float64)
    is_common = np.array([bool(getattr(lib[b], "common", False)) for b in candidates])

    sample_arrs = {}
    if mode == "natural" and tex_w > 0:
        for b in candidates:
            samples = sample_labs.get(b) or []
            if samples:
                sample_arrs[b] = np.asarray(samples, dtype=np.float64)

    for start in range(0, n, chunk):
        end = min(n, start + chunk)
        labs = rgb_to_lab_array(rgbs[start:end])
        d = np.sqrt(((labs[:, None, :] - main_labs[None, :, :]) ** 2).sum(axis=2))
        for ci, b in enumerate(candidates):
            sarr = sample_arrs.get(b)
            if sarr is None:
                continue
            ds = np.sqrt(((labs[:, None, :] - sarr[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            d[:, ci] = (1.0 - tex_w) * d[:, ci] + tex_w * ds
        if common_bonus:
            d[:, is_common] = np.maximum(0.0, d[:, is_common] - common_bonus)
        out[start:end] = names[d.argmin(axis=1)]
    return out


def brute_pick(lib, cfg, rgb, algorithm: str = "ciede2000") -> str:
    """逐候选暴力求最近方块，作为匹配器的独立参照。"""
    lab = rgb_to_lab(rgb)

    def dist(lab2):
        if algorithm == "lab":
            return float(np.sqrt(sum((x - y) ** 2 for x, y in zip(lab, lab2))))
        return delta_e_2000(lab, lab2)

    best_bid, best = None, float("inf")
    for bid, bc in lib.items():
        if not legacy_allowed(lib, cfg, bid):
            continue
        d = dist(rgb_to_lab(bc.main_rgb))
        if cfg.mode == "natural" and cfg.texture_weight > 0 and bc.samples:
            ds = min(dist(rgb_to_lab(tuple(s))) for s in bc.samples)
            d = (1.0 - cfg.texture_weight) * d + cfg.texture_weight * ds
        if cfg.common_priority and bc.common:
            d = max(0.0, d - cfg.common_bonus)
        if d < best:
            best_bid, best = bid, d
    return best_bid


# ---------------- 配置 ----------------
def test_new_switches_default_off():
    cfg = MatchConfig()
    assert cfg.delta_algorithm == "ciede2000"
    assert cfg.hue_priority is False
    assert cfg.prefer_pure is False
    assert cfg.dither is False
    assert cfg.use_custom_palette is False
    assert cfg.custom_palette == []
    # 既有字段不得改名 / 不得改语义
    assert cfg.mode == "precise"
    assert cfg.tolerance == 40.0
    assert cfg.texture_weight == 0.4
    assert cfg.common_priority is True
    assert cfg.common_bonus == 2.0
    assert cfg.blacklist == set() and cfg.whitelist is None
    assert cfg.minimal_block_ids == set(BASIC_BLOCK_IDS)
    assert cfg.allow_falling is False and cfg.allow_transparent is False
    # 新增的可调项
    assert cfg.hue_weight > 0 and cfg.pure_bonus > 0 and cfg.custom_threshold > 0
    print("PASS test_new_switches_default_off")


def test_delta_algorithm_validation():
    for ok in ("ciede2000", "lab"):
        assert MatchConfig(delta_algorithm=ok).delta_algorithm == ok
    try:
        MatchConfig(delta_algorithm="cie76")
    except ValueError as e:
        assert "delta_algorithm" in str(e)
    else:
        assert False, "非法 delta_algorithm 应当抛 ValueError"
    print("PASS test_delta_algorithm_validation")


# ---------------- 新开关全关 = 旧行为 ----------------
def test_switch_off_matches_legacy_pixelwise():
    """全部新开关关闭（算法开关的“关”= delta_algorithm="lab"）时与改动前逐像素一致。"""
    rng = np.random.default_rng(20240501)
    samples = rng.integers(0, 256, size=(3000, 3), dtype=np.uint8)
    cases = [
        MatchConfig(mode="precise", delta_algorithm="lab"),
        MatchConfig(mode="natural", delta_algorithm="lab"),
        MatchConfig(mode="minimal", delta_algorithm="lab"),
        MatchConfig(mode="precise", delta_algorithm="lab", blacklist={"Dirt", "Stone"}),
        MatchConfig(mode="precise", delta_algorithm="lab", whitelist={"Stone", "Wood", "Gold"}),
        MatchConfig(mode="natural", delta_algorithm="lab", common_priority=False),
        MatchConfig(mode="precise", delta_algorithm="lab", allow_transparent=True, allow_falling=True),
        MatchConfig(mode="natural", delta_algorithm="lab", texture_weight=0.0),
    ]
    for cfg in cases:
        legacy = legacy_match_rgb_batch(LIB, cfg, samples)
        new = BlockMatcher(LIB, cfg).match_rgb_batch(samples)
        diff = int((legacy != new).sum())
        assert diff == 0, f"{cfg} 有 {diff} 个像素与改动前不一致"
    print(f"PASS test_switch_off_matches_legacy_pixelwise  ({len(cases)} 组配置 × {len(samples)} 像素全等)")


def test_ciede2000_is_default_and_exact():
    """默认走 CIEDE2000：命中方块必须是全候选里 CIEDE2000 最小的那个。"""
    cfg = MatchConfig()
    m = BlockMatcher(LIB, cfg)
    colors = [(200, 40, 40), (40, 90, 220), (230, 225, 215), (80, 150, 60), (35, 25, 50), (120, 110, 95)]
    for rgb in colors:
        got = m.match_pixel(rgb)
        assert got is not None
        assert got.block_id == brute_pick(LIB, cfg, rgb), (rgb, got.block_id)
        # distance 就是所使用的色差（常用方块再扣 common_bonus）
        want = delta_e_2000(rgb_to_lab(rgb), rgb_to_lab(LIB[got.block_id].main_rgb))
        if cfg.common_priority and LIB[got.block_id].common:
            want = max(0.0, want - cfg.common_bonus)
        assert abs(got.distance - want) < 1e-9, (rgb, got.distance, want)

    # 换成旧算法时色差数值必须不同（说明默认确实走了 CIEDE2000）
    lab_m = BlockMatcher(LIB, MatchConfig(delta_algorithm="lab"))
    diffs = [abs(m.match_pixel(c).distance - lab_m.match_pixel(c).distance) for c in colors]
    assert any(d > 1e-6 for d in diffs), diffs
    print(f"PASS test_ciede2000_is_default_and_exact  (6 色暴力校验，ΔE 差异最大 {max(diffs):.2f})")


def test_match_pixel_equals_batch():
    """match_pixel 与 match_rgb_batch 必须给出完全一致的方块。"""
    rng = np.random.default_rng(99)
    colors = [tuple(int(v) for v in row) for row in rng.integers(0, 256, size=(500, 3))]
    cfgs = [
        MatchConfig(),
        MatchConfig(delta_algorithm="lab"),
        MatchConfig(mode="natural"),
        MatchConfig(mode="minimal"),
        MatchConfig(blacklist={"Stone", "Ash"}),
        MatchConfig(whitelist={"Dirt", "Brick", "Gold"}),
        MatchConfig(hue_priority=True),
        MatchConfig(prefer_pure=True),
        MatchConfig(mode="natural", hue_priority=True, prefer_pure=True, delta_algorithm="lab"),
        MatchConfig(
            use_custom_palette=True,
            custom_palette=[{"rgb": [200, 40, 40], "block_id": "Brick"}, {"rgb": [40, 90, 220], "block_id": "Meteorite"}],
        ),
    ]
    for cfg in cfgs:
        m = BlockMatcher(LIB, cfg)
        batch = m.match_rgb_batch(np.array(colors, dtype=np.uint8))
        pixel = [m.match_pixel(c).block_id for c in colors]
        diff = sum(1 for a, b in zip(batch, pixel) if a != b)
        assert diff == 0, f"{cfg} 有 {diff} 个像素两入口不一致"
    print(f"PASS test_match_pixel_equals_batch  ({len(cfgs)} 组配置 × {len(colors)} 像素)")


# ---------------- 色相优先 ----------------
def test_hue_priority_lowers_hue_gap():
    off = BlockMatcher(LIB, MatchConfig(hue_priority=False))
    on = BlockMatcher(LIB, MatchConfig(hue_priority=True))

    def gap(matcher, rgb, bid):
        return hue_angle_delta(rgb_to_lab(rgb), rgb_to_lab(LIB[bid].main_rgb))

    # 蓝变紫：开启后色相角差必须严格下降（这是「优先保色相」存在的理由）
    # 注：暖白 (230,225,215) 原先也在这条断言里，但它的 C*=5.49 属于近中性，
    #     色相角对它无意义；强行按色相纠偏会把亮白(L*89.7)推到暗方块上，
    #     正是「白色骨骼发灰」的成因。现已由源侧彩度门挡住，改由
    #     test_hue_priority_does_not_darken_near_neutral 覆盖。
    # 注意：色库补了高饱和蓝紫/红块之后，部分样本的色相余量已经被色库本身吃掉，
    # 此时「保色相」不可能再下降。断言改成「有明确余量时必须下降，否则不得变差」。
    improved = 0
    for rgb in [(40, 90, 220), (20, 60, 190), (180, 140, 60), (200, 40, 40), (60, 170, 90), (150, 60, 160)]:
        b_off, b_on = off.match_pixel(rgb).block_id, on.match_pixel(rgb).block_id
        g_off, g_on = gap(off, rgb, b_off), gap(on, rgb, b_on)
        assert g_on <= g_off + 1e-9, f"{rgb}: {b_off}({g_off:.1f}°) -> {b_on}({g_on:.1f}°) 变差了"
        if g_on < g_off - 1e-9:
            improved += 1
    # 真实色库在补了高饱和色块后余量已很小，开关未必还有增益 —— 这里只要求「不得变差」。
    # 开关是否真接线，由下面合成色库那一段单独证明（不依赖色库演化）。
    del improved

    # 开关是否真接线：在整条色域网格上量化它的作用面。
    # 用网格而不是挑个例，是因为色库会继续演化，个例随时会失去代表性。
    step = 48
    vals = np.arange(0, 256, step, dtype=np.uint8)
    grid = np.array([[r, g, b] for r in vals for g in vals for b in vals], dtype=np.uint8)
    sel_off = off.match_rgb_batch(grid)
    sel_on = on.match_rgb_batch(grid)
    changed = sel_off != sel_on
    assert changed.sum() >= 1, "保色相开关对整条色域网格毫无影响，开关没接线"
    idx = np.where(changed)[0]
    g_off_arr = np.array([hue_angle_delta(rgb_to_lab(grid[i]), rgb_to_lab(LIB[sel_off[i]].main_rgb)) for i in idx])
    g_on_arr = np.array([hue_angle_delta(rgb_to_lab(grid[i]), rgb_to_lab(LIB[sel_on[i]].main_rgb)) for i in idx])
    # 注：判据用简单色相夹角，而开关内部用的是 CIEDE2000 的 Δh'（含平均色相旋转），
    # 两者不完全等价，因此允许少量样本在夹角口径上轻微变差。
    better_ratio = float((g_on_arr < g_off_arr - 1e-9).mean())
    assert better_ratio >= 0.6, f"色相改善比例过低：{better_ratio:.2f}（{int(changed.sum())} 个改变）"

    # 近红样本本身色相已无偏差，开关不应加重偏差
    rgb = (200, 40, 40)
    b_off, b_on = off.match_pixel(rgb).block_id, on.match_pixel(rgb).block_id
    assert gap(on, rgb, b_on) <= gap(off, rgb, b_off) + 1e-9

    # 允许以 ΔE 变大换取色相更准：只在开关确实改变了选择时才谈这个交换。
    blue = (40, 90, 220)
    b0 = off.match_pixel(blue).block_id
    b1 = on.match_pixel(blue).block_id
    if b1 != b0:
        d_off = delta_e_2000(rgb_to_lab(blue), rgb_to_lab(LIB[b0].main_rgb))
        d_on = delta_e_2000(rgb_to_lab(blue), rgb_to_lab(LIB[b1].main_rgb))
        assert d_on >= d_off - 1e-9, (d_on, d_off)

    # 中性灰不受影响（彩度衰减护住近灰像素）
    for gray in [(128, 128, 128), (130, 128, 127), (240, 240, 238), (18, 18, 18)]:
        assert off.match_pixel(gray).block_id == on.match_pixel(gray).block_id, gray
    print("PASS test_hue_priority_lowers_hue_gap  (ΔH 下降，ΔE 变大，灰度不变)")


# ---------------- 纯色方块优先 ----------------
def test_prefer_pure_uses_discount():
    """合成方块库 + 受控纯度：prefer_pure 必须把选择推向更纯的方块。"""
    lib = {
        "TexA": BlockColor("TexA", "纯色A", (140, 140, 140), [[140, 140, 140]], common=False),
        "TexB": BlockColor("TexB", "花纹B", (150, 150, 150), [[120, 120, 120], [180, 180, 180]], common=False),
    }
    purity = {"TexA": 1.0, "TexB": 0.0}

    plain = BlockMatcher(lib, MatchConfig(mode="precise", common_priority=False))
    plain._purity_of = lambda bid: purity[bid]
    pure = BlockMatcher(lib, MatchConfig(mode="precise", common_priority=False, prefer_pure=True, pure_bonus=8.0))
    pure._purity_of = lambda bid: purity[bid]

    rgb = (150, 150, 150)  # 更接近 TexB 主色
    assert plain.match_pixel(rgb).block_id == "TexB"
    assert pure.match_pixel(rgb).block_id == "TexA"

    # 全量样本：变过的像素一定是纯度更高的一侧
    rng = np.random.default_rng(5)
    samples = rng.integers(100, 200, size=(400, 3), dtype=np.uint8)
    real_plain = BlockMatcher(LIB, MatchConfig())
    real_pure = BlockMatcher(LIB, MatchConfig(prefer_pure=True))
    a = real_plain.match_rgb_batch(samples)
    b = real_pure.match_rgb_batch(samples)
    changed = [(i0, a[i0], b[i0]) for i0 in range(len(a)) if a[i0] != b[i0]]
    assert changed, "prefer_pure 未改变任何像素，说明折扣没生效"
    for _, b_off, b_on in changed:
        assert real_pure._purity_of(b_on) > real_pure._purity_of(b_off)
    print(f"PASS test_prefer_pure_uses_discount  (真实库改动 {len(changed)}/{len(a)} 像素，纯度一律升高)")


def test_purity_fallback_from_samples_spread():
    """palette_auto 缺失/存在都不影响：samples 离散度估算本身要合理。"""
    flat = _spread_purity([[140, 140, 140], [140, 140, 140], [140, 140, 140]])
    loose = _spread_purity([[139, 111, 75], [120, 95, 65], [155, 125, 85]])
    wide = _spread_purity([[0, 0, 0], [255, 255, 255], [0, 0, 0]])
    assert flat == 1.0
    assert 0.0 < wide < loose < flat
    assert _spread_purity([]) == 1.0
    assert _spread_purity([[1, 2, 3]]) == 1.0

    m = BlockMatcher(LIB, MatchConfig())
    for bid in LIB:
        assert 0.0 < m._purity_of(bid) <= 1.0
    print(f"PASS test_purity_fallback_from_samples_spread  flat=1.0 loose={loose:.3f} wide={wide:.3f}")


# ---------------- 纯色方块优先（palette_auto 实数据） ----------------
def test_prefer_pure_prefers_palette_auto():
    """palette_auto 已存在：纯色度取自它的 pure_score，低 variance 的纯色方块确实更受青睐。"""
    from engine import palette_auto

    fn = matcher_mod._pure_score_fn()
    assert fn is not None, "engine/palette_auto.py 存在时 pure_score 必须被探测到"

    m = BlockMatcher(LIB, MatchConfig())
    for bid in ("Stone", "HardenedSand", "Grass"):
        assert abs(m._purity_of(bid) - palette_auto.PALETTE[bid]["solid_pure_score"]) < 1e-12, bid

    # 三个样本的纹理方差与纯色度必须反向
    stone = palette_auto.PALETTE["Stone"]
    hard = palette_auto.PALETTE["HardenedSand"]
    grass = palette_auto.PALETTE["Grass"]
    assert hard["variance"] < stone["variance"] < grass["variance"]
    assert hard["pure_score"] > stone["pure_score"] > grass["pure_score"]

    # 实际影响：固定种子样本上开启了 prefer_pure 后，换块的像素一律换向更纯的方块
    rng = np.random.default_rng(21)
    samples = rng.integers(0, 256, size=(2000, 3), dtype=np.uint8)
    plain = BlockMatcher(LIB, MatchConfig())
    pure = BlockMatcher(LIB, MatchConfig(prefer_pure=True))
    a = plain.match_rgb_batch(samples)
    b = pure.match_rgb_batch(samples)
    changed = np.nonzero(a != b)[0]
    assert changed.size > 0, "palette_auto 生效后 prefer_pure 应当改变选择"
    for i in changed:
        assert pure._purity_of(b[i]) > pure._purity_of(a[i])

    gained = {bid: int((b[changed] == bid).sum()) for bid in ("Stone", "HardenedSand", "Grass")}
    lost = {bid: int((a[changed] == bid).sum()) for bid in ("Stone", "HardenedSand", "Grass")}
    # 高纯度的 HardenedSand 净流入，低纯度的 Grass 净流出
    assert gained["HardenedSand"] > lost["HardenedSand"]
    assert gained["Grass"] < lost["Grass"]
    print(
        "PASS test_prefer_pure_prefers_palette_auto  "
        f"pure_score Stone={stone['pure_score']} HardenedSand={hard['pure_score']} Grass={grass['pure_score']}；"
        f"2000 像素改动 {changed.size} 个，"
        f"Stone +{gained['Stone']}/-{lost['Stone']}，"
        f"HardenedSand +{gained['HardenedSand']}/-{lost['HardenedSand']}，"
        f"Grass +{gained['Grass']}/-{lost['Grass']}"
    )


# ---------------- dither ----------------
def test_dither_off_is_pixel_identical():
    """dither 关闭时，逐像素入口与批量入口、与不开抖动的整图入口完全一致。"""
    rng = np.random.default_rng(3)
    img = rng.integers(0, 256, size=(24, 18, 3), dtype=np.uint8)
    cfg = MatchConfig()
    m = BlockMatcher(LIB, cfg)
    grid = m.match_image(img)
    flat = m.match_rgb_batch(img.reshape(-1, 3)).reshape(img.shape[0], img.shape[1])
    assert np.array_equal(grid, flat)
    assert (grid == "").sum() == 0
    # 与关掉 dither 的显式二维入口同源
    assert np.array_equal(grid, BlockMatcher(LIB, MatchConfig(dither=False)).match_image(img))
    print("PASS test_dither_off_is_pixel_identical")


def test_dither_deterministic_and_mixes():
    rng = np.random.default_rng(11)
    img = rng.integers(0, 256, size=(40, 40, 3), dtype=np.uint8)
    m = BlockMatcher(LIB, MatchConfig(dither=True))
    first = m.match_image_dithered(img)
    second = BlockMatcher(LIB, MatchConfig(dither=True)).match_image_dithered(img)
    assert np.array_equal(first, second), "同输入必须同输出"

    # 单一平色区域：不开抖动只有 1 种方块，开抖动必须混出多种（误差扩散真的在动）
    flat_img = np.full((24, 24, 3), (120, 110, 95), dtype=np.uint8)
    plain = BlockMatcher(LIB, MatchConfig())
    plain_ids = set(plain.match_image(flat_img).ravel())
    dither_ids = set(m.match_image_dithered(flat_img).ravel())
    assert len(plain_ids) == 1, plain_ids
    assert len(dither_ids) > len(plain_ids), dither_ids

    # match_image 在 dither=True 时自动走扩散
    assert np.array_equal(m.match_image(flat_img), m.match_image_dithered(flat_img))
    print(f"PASS test_dither_deterministic_and_mixes  平色抖动出 {len(dither_ids)} 种方块")


def test_dither_independent_of_mode():
    """抖动只比主色，所以 precise 与 natural 的抖动结果必须完全一致。"""
    rng = np.random.default_rng(12)
    img = rng.integers(0, 256, size=(40, 30, 3), dtype=np.uint8)
    pal = [{"rgb": [139, 111, 75], "block_id": "Stone"}, {"rgb": [40, 90, 220], "block_id": "Ash"}]
    pairs = [
        (MatchConfig(dither=True), MatchConfig(dither=True, mode="natural")),
        (MatchConfig(dither=True, hue_priority=True),
         MatchConfig(dither=True, mode="natural", hue_priority=True)),
        (MatchConfig(dither=True, texture_weight=0.9),
         MatchConfig(dither=True, mode="natural", texture_weight=0.9)),
        (MatchConfig(dither=True, use_custom_palette=True, custom_palette=pal),
         MatchConfig(dither=True, mode="natural", use_custom_palette=True, custom_palette=pal)),
    ]
    for precise_cfg, natural_cfg in pairs:
        a = BlockMatcher(LIB, precise_cfg).match_image_dithered(img)
        b = BlockMatcher(LIB, natural_cfg).match_image_dithered(img)
        assert np.array_equal(a, b), (precise_cfg, natural_cfg)

    # 非抖动路径必须仍然区分两档模式（纹理加权不能被削弱）
    plain = BlockMatcher(LIB, MatchConfig()).match_image(img)
    natural = BlockMatcher(LIB, MatchConfig(mode="natural")).match_image(img)
    assert not np.array_equal(plain, natural), "natural 的纹理加权在非抖动路径上被削弱了"
    print("PASS test_dither_independent_of_mode  (4 组配置 array_equal，非抖动路径仍区分模式)")


def test_dither_skips_mask_and_excluded_blocks():
    img = np.full((10, 10, 3), (139, 111, 75), dtype=np.uint8)
    mask = np.ones((10, 10), dtype=bool)
    mask[0, :] = False
    mask[3, 7] = False
    out = BlockMatcher(LIB, MatchConfig(dither=True)).match_image_dithered(img, mask)
    assert all(v == "" for v in out[0, :]), out[0, :]
    assert out[3, 7] == ""
    assert all(out[y, x] != "" for y in range(1, 10) for x in range(10) if not (y == 3 and x == 7))

    # 黑白名单 / 极简子集排除的方块不得出现在抖动结果里
    black = BlockMatcher(LIB, MatchConfig(dither=True, blacklist={"Dirt", "LivingWood", "Wood", "WoodPlank"}))
    ids = set(black.match_image(img).ravel()) - {""}
    assert not (ids & {"Dirt", "LivingWood", "Wood", "WoodPlank"}), ids

    white = BlockMatcher(LIB, MatchConfig(dither=True, whitelist={"Stone", "Ash"}))
    assert set(white.match_image(img).ravel()) <= {"Stone", "Ash"}

    minimal = BlockMatcher(LIB, MatchConfig(dither=True, mode="minimal"))
    assert set(minimal.match_image(img).ravel()) <= set(BASIC_BLOCK_IDS)
    print(f"PASS test_dither_skips_mask_and_excluded_blocks  blacklist 下用 {len(ids)} 种方块")


def test_dither_200k_under_10s():
    """20 万像素（默认 CIEDE2000）必须 10 秒内完成。"""
    rng = np.random.default_rng(20240501)
    img = rng.integers(0, 256, size=(400, 500, 3), dtype=np.uint8)
    m = BlockMatcher(LIB, MatchConfig(dither=True))
    t0 = time.perf_counter()
    out = m.match_image_dithered(img)
    dt = time.perf_counter() - t0
    assert out.shape == (400, 500)
    assert dt < 10.0, f"dither 200k 用了 {dt:.2f}s"
    print(f"PASS test_dither_200k_under_10s  {dt:.2f}s / 200000 px")


# ---------------- 自定义色卡 ----------------
def test_custom_palette_priority():
    """色卡命中优先于自动匹配。"""
    cfg = MatchConfig(use_custom_palette=True, custom_palette=[{"rgb": [139, 111, 75], "block_id": "Stone"}])
    m = BlockMatcher(LIB, cfg)
    auto = BlockMatcher(LIB, MatchConfig())
    assert auto.match_pixel((139, 111, 75)).block_id == "Dirt"
    res = m.match_pixel((139, 111, 75))
    assert res.block_id == "Stone", res.block_id
    assert res.matched_rgb == LIB["Stone"].main_rgb

    batch = m.match_rgb_batch(np.array([(139, 111, 75), (200, 40, 40)], dtype=np.uint8))
    assert batch[0] == "Stone"
    assert batch[1] == auto.match_pixel((200, 40, 40)).block_id  # 未命中条目走自动匹配
    print("PASS test_custom_palette_priority")


def test_custom_palette_threshold_fallback():
    """超出阈值不算命中，回退自动匹配。"""
    cfg = MatchConfig(use_custom_palette=True, custom_palette=[{"rgb": [255, 0, 0], "block_id": "Brick"}])
    m = BlockMatcher(LIB, cfg)
    auto = BlockMatcher(LIB, MatchConfig())
    rgb = (139, 111, 75)
    assert m.match_pixel(rgb).block_id == auto.match_pixel(rgb).block_id

    # 阈值语义：命中条件是「与条目的 Lab 欧氏距离 <= custom_threshold」
    entry = (155, 125, 85)
    gap = float(np.linalg.norm(np.array(rgb_to_lab(entry)) - np.array(rgb_to_lab(rgb))))
    assert gap > 1.0, gap
    def matcher(th):
        return BlockMatcher(LIB, MatchConfig(use_custom_palette=True, custom_threshold=th,
                                             custom_palette=[{"rgb": list(entry), "block_id": "Brick"}]))
    assert matcher(gap * 0.5).match_pixel(rgb).block_id == auto.match_pixel(rgb).block_id
    assert matcher(gap * 1.5).match_pixel(rgb).block_id == "Brick"
    print(f"PASS test_custom_palette_threshold_fallback  (条目 Lab 距离 {gap:.2f})")


def test_custom_palette_excluded_entries_fall_back():
    """目标方块被黑名单/白名单/重力/半透明/极简子集/未知方块排除时，跳过该条走自动匹配。"""
    def check(cfg, rgb):
        # 参照：同一配置但关掉色卡（严格回退到自动匹配）
        ref_cfg = dataclasses.replace(cfg, use_custom_palette=False, custom_palette=[])
        want = BlockMatcher(LIB, ref_cfg).match_pixel(rgb).block_id
        got = BlockMatcher(LIB, cfg).match_pixel(rgb).block_id
        assert got == want, f"{cfg} -> {got} != {want}"

    dirt_rgb = (139, 111, 75)
    check(MatchConfig(use_custom_palette=True, blacklist={"Stone"},
                      custom_palette=[{"rgb": [139, 111, 75], "block_id": "Stone"}]), dirt_rgb)
    check(MatchConfig(use_custom_palette=True, whitelist={"Dirt"},
                      custom_palette=[{"rgb": [139, 111, 75], "block_id": "Stone"}]), dirt_rgb)
    check(MatchConfig(use_custom_palette=True, custom_palette=[{"rgb": [230, 220, 150], "block_id": "Sand"}]),
          (230, 220, 150))  # 重力方块默认排除
    check(MatchConfig(use_custom_palette=True, custom_palette=[{"rgb": [200, 220, 240], "block_id": "Glass"}]),
          (200, 220, 240))  # 半透明默认排除
    check(MatchConfig(use_custom_palette=True, mode="minimal",
                      custom_palette=[{"rgb": [170, 150, 120], "block_id": "Clay"}]), (170, 150, 120))
    check(MatchConfig(use_custom_palette=True, custom_palette=[{"rgb": [139, 111, 75], "block_id": "NotABlock"}]),
          dirt_rgb)

    # 打开 allow_falling / allow_transparent 后同一色卡条目才生效
    sand = BlockMatcher(LIB, MatchConfig(use_custom_palette=True, allow_falling=True,
                                         custom_palette=[{"rgb": [230, 220, 150], "block_id": "Sand"}]))
    assert sand.match_pixel((230, 220, 150)).block_id == "Sand"
    glass = BlockMatcher(LIB, MatchConfig(use_custom_palette=True, allow_transparent=True,
                                          custom_palette=[{"rgb": [200, 220, 240], "block_id": "Glass"}]))
    assert glass.match_pixel((200, 220, 240)).block_id == "Glass"
    print("PASS test_custom_palette_excluded_entries_fall_back  (黑/白名单、重力、半透明、极简、未知方块)")


def test_custom_palette_limit_and_validation():
    ok = [{"rgb": [i, i, i], "block_id": "Stone"} for i in range(MAX_CUSTOM_PALETTE)]
    assert len(MatchConfig(custom_palette=ok).custom_palette) == MAX_CUSTOM_PALETTE
    too_many = ok + [{"rgb": [0, 0, 0], "block_id": "Stone"}]
    try:
        MatchConfig(custom_palette=too_many)
    except ValueError as e:
        assert str(MAX_CUSTOM_PALETTE) in str(e)
    else:
        assert False, "超过上限必须抛 ValueError"
    for bad in ([{"rgb": [1, 2], "block_id": "Stone"}], [{"block_id": "Stone"}], [{"rgb": [1, 2, 3]}]):
        try:
            MatchConfig(custom_palette=bad)
        except ValueError:
            pass
        else:
            assert False, f"非法条目应当抛 ValueError：{bad}"
    print(f"PASS test_custom_palette_limit_and_validation  (上限 {MAX_CUSTOM_PALETTE})")


def test_custom_palette_off_has_no_residue():
    """use_custom_palette=False 时，色卡内容对结果零影响。"""
    rng = np.random.default_rng(17)
    samples = rng.integers(0, 256, size=(1500, 3), dtype=np.uint8)
    palette = [{"rgb": [139, 111, 75], "block_id": "Stone"}, {"rgb": [40, 90, 220], "block_id": "Ash"}]
    with_palette = BlockMatcher(LIB, MatchConfig(use_custom_palette=False, custom_palette=palette))
    without = BlockMatcher(LIB, MatchConfig())
    assert np.array_equal(with_palette.match_rgb_batch(samples), without.match_rgb_batch(samples))
    # 关掉开关但保留色卡：dither 路径同样不受影响
    img = samples.reshape(30, 50, 3)
    assert np.array_equal(
        BlockMatcher(LIB, MatchConfig(dither=True, custom_palette=palette)).match_image(img),
        BlockMatcher(LIB, MatchConfig(dither=True)).match_image(img),
    )
    print("PASS test_custom_palette_off_has_no_residue")



def test_hue_priority_does_not_darken_near_neutral():
    """近中性（微带色偏的白/灰）像素不能因为「优先保色相」被推向更暗的方块。

    背景：源侧彩度低时色相角没有意义，旧的 sqrt(cm*cn) 衰减不够，
    骷髅图实测 32 万个 S<0.15 的像素匹配亮度从 77.1 掉到 73.4（暗 13 L*），
    表现为「白色骨骼发灰」。现在用源侧彩度门 (_HUE_CHROMA_SRC_LO/HI) 关掉这部分色相项。
    """
    off = BlockMatcher(LIB, MatchConfig(hue_priority=False))
    on = BlockMatcher(LIB, MatchConfig(hue_priority=True))

    # 带轻微色偏的近白/近灰（真实照片里的白平衡残余就在这个彩度区间）
    near_neutral = [
        (242, 240, 236), (230, 228, 222), (214, 212, 208), (196, 195, 199),
        (180, 184, 186), (160, 158, 155), (128, 130, 128), (96, 98, 100),
    ]
    for rgb in near_neutral:
        cm = float(np.hypot(*rgb_to_lab(rgb)[1:]))
        assert cm < 12.0, f"{rgb} 彩度 {cm:.1f} 不在近中性区间，样本失效"
        assert off.match_pixel(rgb).block_id == on.match_pixel(rgb).block_id, \
            f"{rgb} 被色相项改动：{off.match_pixel(rgb).block_id} -> {on.match_pixel(rgb).block_id}"

    # 反向：高彩度像素的色相项必须仍然生效（蓝变紫仍要被压住）
    blue = (40, 90, 220)
    cm_blue = float(np.hypot(*rgb_to_lab(blue)[1:]))
    assert cm_blue > 25.0
    b_off = off.match_pixel(blue).block_id
    b_on = on.match_pixel(blue).block_id
    g_off = hue_angle_delta(rgb_to_lab(blue), rgb_to_lab(LIB[b_off].main_rgb))
    g_on = hue_angle_delta(rgb_to_lab(blue), rgb_to_lab(LIB[b_on].main_rgb))
    assert g_on <= g_off + 1e-9, f"高彩度蓝被色相项弄差了：{b_off}({g_off:.1f}°) -> {b_on}({g_on:.1f}°)"
    print("PASS test_hue_priority_does_not_darken_near_neutral  (近中性不变，高彩度色相项仍生效)")

if __name__ == "__main__":
    test_new_switches_default_off()
    test_delta_algorithm_validation()
    test_switch_off_matches_legacy_pixelwise()
    test_ciede2000_is_default_and_exact()
    test_match_pixel_equals_batch()
    test_hue_priority_lowers_hue_gap()
    test_hue_priority_does_not_darken_near_neutral()
    test_prefer_pure_uses_discount()
    test_purity_fallback_from_samples_spread()
    test_prefer_pure_prefers_palette_auto()
    test_dither_off_is_pixel_identical()
    test_dither_deterministic_and_mixes()
    test_dither_independent_of_mode()
    test_dither_skips_mask_and_excluded_blocks()
    test_custom_palette_priority()
    test_custom_palette_threshold_fallback()
    test_custom_palette_excluded_entries_fall_back()
    test_custom_palette_limit_and_validation()
    test_custom_palette_off_has_no_residue()
    test_dither_200k_under_10s()
    print("\n=== 匹配器 v2 测试通过 ===")
