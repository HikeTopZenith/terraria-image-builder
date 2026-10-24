from __future__ import annotations

"""阶段3 外端管线 v2 测试：贴图色库接管 / dither 主流程 / 预览与蓝图同源 / .tibp 向前兼容。

约定：本文件只测本批次改动的接线，不重复 matcher_v2 的算法细节。
"""

import json
import os
import sys
import tempfile
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import engine.blocks as blocks_mod
import engine.preview as preview_mod
from engine import palette_auto
from engine.blocks import BLOCKS, build_block_library
from engine.contract import (
    MAX_CUSTOM_PALETTE,
    PROJECT_FORMAT,
    BlockEntry,
    Blueprint,
    BlueprintMetadata,
    ChunkEntry,
    build_settings_dict,
    load_project,
    normalize_settings,
    save_project,
    validate_blueprint,
)
from engine.engine import BuildEngine, EngineConfig, build_match_config
from engine.matcher import BlockMatcher, MatchConfig
from engine.presets import NEW_MATCH_DEFAULTS, PRESETS, get_preset
from engine.preview import render_blueprint, render_grid

TMP = tempfile.mkdtemp(prefix="terraria_builder_pipeline_")
HARDCODED = {b.block_id: b for b in BLOCKS}
SIX_KEYS = (
    "delta_algorithm",
    "hue_priority",
    "prefer_pure",
    "dither",
    "use_custom_palette",
    "custom_palette",
)


def flat_arr(rgb, h=12, w=12):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[...] = rgb
    return arr


def make_blueprint():
    return Blueprint(
        width=2,
        height=2,
        chunk_size=4,
        chunks=[
            ChunkEntry(
                chunk_index=0,
                offset_x=0,
                offset_y=0,
                width=2,
                height=2,
                blocks=[
                    BlockEntry(0, 0, "Dirt", 0),
                    BlockEntry(1, 1, "Stone", 0),
                ],
            )
        ],
        metadata=BlueprintMetadata(
            total_blocks=2,
            total_chunks=1,
            match_mode="precise",
            processing_time_ms=1.0,
            created_at="now",
            source_image={},
            config={},
        ),
    )


# ---------------- 任务1：贴图固有色接管 ----------------
def test_texture_palette_takeover():
    """默认色库的 main_rgb / samples 必须来自 palette_auto 的实心帧口径。"""
    lib = build_block_library()
    for bid, entry in palette_auto.PALETTE.items():
        solid = entry.get("solid_rgb")
        assert solid is not None, f"{bid} 缺 solid_rgb，本测试需要实心帧口径"
        assert lib[bid].main_rgb == tuple(solid), (bid, lib[bid].main_rgb, solid)
        samples = [list(s) for s in (entry.get("samples") or [])]
        assert lib[bid].samples == samples, bid

    # 取色确实被换掉了（Stone 旧硬编码 140，贴图实心 155）
    assert lib["Stone"].main_rgb != tuple(HARDCODED["Stone"].main_rgb)

    # 非颜色字段一律保留
    for bid, bc in lib.items():
        old = HARDCODED[bid]
        assert bc.name == old.name
        assert bc.common == old.common
        assert bc.transparent == old.transparent
        assert bc.falling == old.falling
    print(f"PASS test_texture_palette_takeover  ({len(palette_auto.PALETTE)} 个方块取自 solid_rgb)")


def test_palette_fallback_three_levels():
    """三级回退：solid_rgb -> avg_rgb -> 硬编码 main_rgb；palette 整体缺失也要活。"""
    entry = palette_auto.PALETTE["Stone"]
    saved = dict(entry)
    try:
        entry.pop("solid_rgb")
        mid = build_block_library()["Stone"].main_rgb
        assert mid == tuple(saved["avg_rgb"]), ("第二级 avg_rgb 未生效", mid)

        entry.pop("avg_rgb")
        last = build_block_library()["Stone"].main_rgb
        assert last == tuple(HARDCODED["Stone"].main_rgb), ("第三级硬编码未生效", last)
    finally:
        entry.update(saved)

    # palette_auto 不可用（导入失败 / 无条目）：整库静默回退硬编码，不抛异常
    original = blocks_mod._texture_entry
    blocks_mod._texture_entry = lambda block_id: None
    try:
        lib = build_block_library()
        for bc in lib.values():
            assert bc.main_rgb == tuple(HARDCODED[bc.block_id].main_rgb)
            assert bc.samples == [list(s) for s in HARDCODED[bc.block_id].samples]
    finally:
        blocks_mod._texture_entry = original
    print("PASS test_palette_fallback_three_levels  (solid_rgb -> avg_rgb -> 硬编码；palette 缺失静默回退)")


def test_legacy_library_switch():
    """use_texture_palette=False 必须与改动前的色库逐块完全一致（A/B 与回归的口径）。"""
    old = build_block_library(use_texture_palette=False)
    for b in BLOCKS:
        # 改动前 build_block_library 直接返回 BLOCKS 里的对象，这里做结构级全等
        assert asdict(old[b.block_id]) == asdict(b), b.block_id
    assert len(old) == len(BLOCKS) == len(set(b.block_id for b in BLOCKS))
    assert old["Stone"].main_rgb != build_block_library()["Stone"].main_rgb
    print(f"PASS test_legacy_library_switch  ({len(BLOCKS)} 块 asdict 全等，贴图口径确有差异)")


# ---------------- 任务3/4：唯一配置构造器 ----------------
def test_build_match_config_defaults_and_passthrough():
    """build_match_config 缺键走默认值、未知键忽略、6 字段按同名透传。"""
    default = build_match_config({})
    ref = MatchConfig()
    # 结构级全等：缺省 settings 与 MatchConfig() 默认值必须逐字段一致，
    # 否则 UI 传空 dict 时会与直接用 MatchConfig() 分叉
    assert asdict(default) == asdict(ref), {
        k: (asdict(default)[k], asdict(ref)[k])
        for k in asdict(ref) if asdict(default)[k] != asdict(ref)[k]
    }
    assert default.blacklist == set() and default.whitelist is None

    palette = [{"rgb": [139, 111, 75], "block_id": "Stone"}]
    cfg = build_match_config({
        "match_mode": "natural",
        "delta_algorithm": "lab",
        "hue_priority": True,
        "prefer_pure": True,
        "dither": True,
        "use_custom_palette": True,
        "custom_palette": palette,
        "unknown_future_key": 123,
    })
    assert cfg.mode == "natural"
    assert cfg.delta_algorithm == "lab"
    assert cfg.hue_priority and cfg.prefer_pure and cfg.dither
    assert cfg.use_custom_palette and cfg.custom_palette == palette
    print("PASS test_build_match_config_defaults_and_passthrough")


def test_engine_config_passthrough_six_fields():
    """EngineConfig 直接接收 6 个同名 kwargs，并原样透传到 MatchConfig 与蓝图 metadata。"""
    palette = [{"rgb": [200, 40, 40], "block_id": "Brick"}]
    ec = EngineConfig(
        delta_algorithm="lab",
        hue_priority=True,
        prefer_pure=True,
        dither=True,
        use_custom_palette=True,
        custom_palette=palette,
        chunk_size=16,
    )
    eng = BuildEngine(ec)
    mc = eng.match_config
    assert mc.delta_algorithm == "lab"
    assert mc.hue_priority is True
    assert mc.prefer_pure is True
    assert mc.dither is True
    assert mc.use_custom_palette is True
    assert mc.custom_palette == palette

    res = eng.build_from_array(flat_arr((200, 40, 40), h=16, w=16))
    meta_cfg = res.blueprint.metadata.config
    assert meta_cfg["delta_algorithm"] == "lab"
    assert meta_cfg["hue_priority"] and meta_cfg["prefer_pure"] and meta_cfg["dither"]
    assert meta_cfg["use_custom_palette"] and meta_cfg["custom_palette"] == palette
    # 蓝图与预览共用同一个 match_config 对象
    assert eng.match_config is eng.matcher.config
    print("PASS test_engine_config_passthrough_six_fields")


# ---------------- 任务2：dither 主流程 ----------------
def test_dither_main_flow_2d_with_alpha():
    """dither=True 时引擎走 2D 路径并传 alpha_mask，结果进入网格与蓝图。"""
    arr = flat_arr((120, 110, 95), h=12, w=12)
    mask = np.ones((12, 12), dtype=bool)
    mask[0, :] = False
    mask[5, 5] = False

    eng = BuildEngine(EngineConfig(dither=True, chunk_size=12))
    res = eng.build_from_array(arr, mask, {"path": "mem"})
    ref = BlockMatcher(eng.lib, eng.match_config).match_image_dithered(arr, mask)
    assert np.array_equal(res.matched_grid, ref), "主流程抖动结果与 2D 入口不同源"

    # alpha_mask 生效：透明像素既不匹配也不扩散
    assert all(res.matched_grid[0, x] == "" for x in range(12))
    assert res.matched_grid[5, 5] == ""
    assert int((res.matched_grid != "").sum()) == int(mask.sum())

    # 平色区域抖动必须混出多种方块，且蓝图合法
    ids = set(res.matched_grid[mask].tolist())
    assert len(ids) > 1, ids
    assert validate_blueprint(res.blueprint) == []
    assert res.blueprint.metadata.total_blocks == int(mask.sum())
    assert res.blueprint.metadata.config["dither"] is True

    # dither=False 时同图只有一种方块（开关确实分流了）
    plain = BuildEngine(EngineConfig(dither=False, chunk_size=12))
    res_plain = plain.build_from_array(arr, mask)
    assert len(set(res_plain.matched_grid[mask].tolist())) == 1
    assert validate_blueprint(res_plain.blueprint) == []
    print(f"PASS test_dither_main_flow_2d_with_alpha  (抖动出 {len(ids)} 种方块，关抖动 1 种)")


def test_dither_off_matches_legacy_batch_path():
    """dither 关闭时引擎的匹配结果必须与一维批量入口逐像素等价（改动前路径）。"""
    rng = np.random.default_rng(2024)
    arr = rng.integers(0, 256, size=(10, 14, 3), dtype=np.uint8)
    mask = np.ones((10, 14), dtype=bool)
    mask[0, 0] = False

    eng = BuildEngine(EngineConfig(use_texture_palette=False, delta_algorithm="lab", chunk_size=14))
    res = eng.build_from_array(arr, mask)

    ref = BlockMatcher(eng.lib, eng.match_config).match_rgb_batch(arr[mask])
    expect = np.full((10, 14), "", dtype=object)
    expect[mask] = ref
    assert np.array_equal(res.matched_grid, expect)
    # 掩码位保持空
    assert res.matched_grid[0, 0] == ""
    print("PASS test_dither_off_matches_legacy_batch_path")


# ---------------- 任务3：预览与蓝图同源 ----------------
def test_preview_and_blueprint_share_library():
    lib = build_block_library()
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    arr[0:2, 0:2] = lib["Stone"].main_rgb
    arr[0:2, 2:4] = lib["Dirt"].main_rgb
    arr[2:4, :] = lib["Wood"].main_rgb

    eng = BuildEngine(EngineConfig(chunk_size=32))
    res = eng.build_from_array(arr)

    img_grid = np.array(render_grid(res.matched_grid, library=eng.lib))
    img_bp = np.array(render_blueprint(res.blueprint, library=eng.lib))
    assert np.array_equal(img_grid, img_bp), "预览与蓝图渲染结果不一致"

    # 上色取自贴图色库（Stone 155，而不是旧硬编码 140）
    assert tuple(img_grid[0, 0, :3]) == tuple(eng.lib["Stone"].main_rgb)
    assert tuple(img_grid[0, 0, :3]) != tuple(HARDCODED["Stone"].main_rgb)

    # 预览只上色：输出颜色必须全部来自同一色库，不允许任何颜色换算
    lib_colors = {tuple(bc.main_rgb) for bc in eng.lib.values()}
    painted = {tuple(img_grid[y, x, :3]) for y in range(4) for x in range(4)}
    assert painted <= lib_colors, painted - lib_colors
    assert not hasattr(preview_mod, "BlockMatcher"), "preview 不得持有匹配逻辑"

    # 显式换旧色库时，同一张蓝图必须跟着换色（说明只有一个色库来源）
    old_img = np.array(render_blueprint(res.blueprint, library=build_block_library(use_texture_palette=False)))
    assert tuple(old_img[0, 0, :3]) == tuple(HARDCODED["Stone"].main_rgb)
    print("PASS test_preview_and_blueprint_share_library")


def test_engine_render_preview_uses_own_library():
    eng = BuildEngine(EngineConfig(use_texture_palette=False, chunk_size=8))
    arr = flat_arr(HARDCODED["Stone"].main_rgb, h=4, w=4)
    res = eng.build_from_array(arr)
    img = np.array(eng.render_preview(res))
    assert tuple(img[0, 0, :3]) == tuple(eng.lib[res.matched_grid[0, 0]].main_rgb)
    # 蓝图已载入（matched_grid 为空）时也能渲染
    img_bp = np.array(eng.render_preview(type(res)(**{**res.__dict__, "matched_grid": None})))
    assert img_bp.shape == img.shape
    print("PASS test_engine_render_preview_uses_own_library")


# ---------------- 任务4/5：.tibp 向前兼容 ----------------
def test_settings_dict_and_presets_carry_six_fields():
    s = build_settings_dict()
    assert s["delta_algorithm"] == "ciede2000" and s["custom_palette"] == []
    for key, value in NEW_MATCH_DEFAULTS.items():
        assert key in s and s[key] == value, key

    # 4 套预设显式写死这 6 个默认值（行为不变，只是带上字段）
    for name, preset in PRESETS.items():
        for key, value in NEW_MATCH_DEFAULTS.items():
            assert key in preset, (name, key)
            assert preset[key] == value, (name, key)
        assert get_preset(name)[key] == value

    # 预设 -> settings 往返带上默认值
    round_trip = build_settings_dict(preset="building_simple", **_preset_match_kwargs("building_simple"))
    round_trip["preset"] = "building_simple"
    for key, value in NEW_MATCH_DEFAULTS.items():
        assert round_trip[key] == value, key

    # get_preset 返回的可变字段是独立副本
    p = get_preset("pixel_precise")
    p["custom_palette"].append({"rgb": [1, 2, 3], "block_id": "Stone"})
    assert PRESETS["pixel_precise"]["custom_palette"] == []
    print("PASS test_settings_dict_and_presets_carry_six_fields")


def _preset_match_kwargs(name):
    preset = get_preset(name)
    return {
        "match_mode": preset["match_mode"],
        "tolerance": preset["tolerance"],
        "texture_weight": preset["texture_weight"],
        "common_priority": preset["common_priority"],
    }


def test_project_roundtrip_new_fields():
    bp = make_blueprint()
    palette = [{"rgb": [139, 111, 75], "block_id": "Stone"}]
    settings = build_settings_dict(
        match_mode="natural",
        tolerance=33.0,
        delta_algorithm="lab",
        hue_priority=True,
        prefer_pure=True,
        dither=True,
        use_custom_palette=True,
        custom_palette=palette,
        preset="survival_usable",
    )
    path = os.path.join(TMP, "new_features.tibp")
    save_project(bp, settings, path, {"path": "mem"})
    loaded = load_project(path)

    assert loaded["settings"] == settings
    for key in SIX_KEYS:
        assert loaded["settings"][key] == settings[key], key
    assert loaded["blueprint"].to_dict() == bp.to_dict()
    print("PASS test_project_roundtrip_new_fields  (save_project/load_project 6 字段往返一致)")


def test_old_project_gets_defaults_and_keeps_unknown_fields():
    """旧 .tibp（缺 6 字段）不报错、走默认值、其它字段与未知字段都不丢。"""
    bp = make_blueprint()
    data = {
        "format": PROJECT_FORMAT,
        "version": "1.0",
        "created_at": "2020-01-01T00:00:00+00:00",
        "settings": {
            "match_mode": "natural",
            "tolerance": 12.5,
            "blacklist": ["Sand"],
            "preset": "pixel_precise",
            "future_key": {"a": 1},
        },
        "source": {"path": "old.png"},
        "blueprint": bp.to_dict(),
    }
    path = os.path.join(TMP, "legacy.tibp")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    loaded = load_project(path)
    st = loaded["settings"]
    assert st["match_mode"] == "natural" and st["tolerance"] == 12.5
    assert st["blacklist"] == ["Sand"] and st["preset"] == "pixel_precise"
    for key, value in NEW_MATCH_DEFAULTS.items():
        assert st[key] == value, (key, st[key])
    assert st["future_key"] == {"a": 1}
    assert loaded["source"] == {"path": "old.png"}
    assert loaded["blueprint"].to_dict() == bp.to_dict()
    print("PASS test_old_project_gets_defaults_and_keeps_unknown_fields")


def test_new_project_read_by_old_reader():
    """新文件的未知字段（settings / blueprint metadata）在读取时被忽略而不是崩溃。"""
    bp_dict = make_blueprint().to_dict()
    bp_dict["metadata"]["future_metadata"] = {"x": 1}
    bp_dict["future_top_level"] = [1, 2, 3]
    data = {
        "format": PROJECT_FORMAT,
        "version": "1.0",
        "settings": dict(build_settings_dict(), future_key="keep-me"),
        "source": {},
        "blueprint": bp_dict,
    }
    path = os.path.join(TMP, "future.tibp")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    loaded = load_project(path)
    assert loaded["settings"]["future_key"] == "keep-me"
    assert loaded["blueprint"].width == 2
    # 未知 metadata 被忽略，已知字段照常还原
    assert loaded["blueprint"].metadata.total_blocks == 2
    assert not hasattr(loaded["blueprint"].metadata, "future_metadata")
    print("PASS test_new_project_read_by_old_reader")


def test_custom_palette_limit_enforced():
    too_many = [{"rgb": [i, i, i], "block_id": "Stone"} for i in range(MAX_CUSTOM_PALETTE + 1)]
    for call in (lambda: build_settings_dict(custom_palette=too_many),
                 lambda: normalize_settings({"custom_palette": too_many})):
        try:
            call()
        except ValueError as exc:
            assert str(MAX_CUSTOM_PALETTE) in str(exc)
        else:
            assert False, "超过上限必须抛 ValueError（不做静默截断）"
    ok = normalize_settings({"custom_palette": too_many[:MAX_CUSTOM_PALETTE]})
    assert len(ok["custom_palette"]) == MAX_CUSTOM_PALETTE
    print(f"PASS test_custom_palette_limit_enforced  (上限 {MAX_CUSTOM_PALETTE}，超限报错)")


def test_engine_end_to_end_with_preset_settings():
    """预设 settings -> build_match_config -> 引擎 -> 蓝图/预览 全链路跑通（抖动开/关各一次）。"""
    preset = get_preset("pixel_precise")
    base = build_settings_dict(preset="pixel_precise", **_preset_match_kwargs("pixel_precise"))
    arr = np.zeros((8, 8, 3), dtype=np.uint8)
    arr[:, :4] = build_block_library()["Dirt"].main_rgb
    arr[:, 4:] = build_block_library()["Stone"].main_rgb

    for dither in (False, True):
        settings = dict(base, dither=dither)
        cfg = build_match_config(settings)
        assert cfg.dither is dither
        eng = BuildEngine(EngineConfig(
            match_mode=settings["match_mode"],
            tolerance=settings["tolerance"],
            texture_weight=settings["texture_weight"],
            common_priority=settings["common_priority"],
            delta_algorithm=settings["delta_algorithm"],
            hue_priority=settings["hue_priority"],
            prefer_pure=settings["prefer_pure"],
            dither=settings["dither"],
            use_custom_palette=settings["use_custom_palette"],
            custom_palette=settings["custom_palette"],
            chunk_size=8,
        ))
        res = eng.build_from_array(arr)
        assert validate_blueprint(res.blueprint) == []
        assert res.blueprint.metadata.total_blocks == 64
        img = np.array(eng.render_preview(res))
        assert img.shape == (8, 8, 4)
        # 预览像素必须等于该处方块的色库主色，且不透明
        for (y, x) in ((0, 0), (0, 7), (7, 3)):
            bid = res.matched_grid[y, x]
            assert bid, (y, x)
            assert tuple(img[y, x, :3]) == tuple(eng.lib[bid].main_rgb), (y, x, bid)
            assert img[y, x, 3] == 255
    print("PASS test_engine_end_to_end_with_preset_settings  (dither 开/关各自出蓝图+预览)")


def test_dither_file_pipeline_both_ways():
    """全流程（build_from_file -> 蓝图 -> 预览）在 dither 开/关两种设置下都能跑通。"""
    from PIL import Image

    # 渐变图（不是纯色块）：量化误差非零，抖动才会有可扩散的残差
    xs = np.arange(16, dtype=np.float64)
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    arr[..., 0] = np.clip(60 + xs * 8, 0, 255).astype(np.uint8)[None, :]
    arr[..., 1] = np.clip(100 + xs * 4, 0, 255).astype(np.uint8)[None, :]
    arr[..., 2] = np.clip(150 - xs * 4, 0, 255).astype(np.uint8)[None, :]
    path = os.path.join(TMP, "pipeline_src.png")
    Image.fromarray(arr).save(path)

    out = {}
    for dither in (False, True):
        eng = BuildEngine(EngineConfig(dither=dither, chunk_size=16))
        res = eng.build_from_file(path)
        assert validate_blueprint(res.blueprint) == []
        assert res.blueprint.width == 16 and res.blueprint.height == 16
        assert res.blueprint.metadata.total_blocks == 256
        assert res.blueprint.metadata.config["dither"] is dither
        img = np.array(eng.render_preview(res))
        assert img.shape == (16, 16, 4)
        assert (img[..., 3] == 255).all()
        out[dither] = res.matched_grid
    assert not np.array_equal(out[False], out[True]), "抖动开关没有改变结果，说明没接进主流程"
    print("PASS test_dither_file_pipeline_both_ways  (dither 开/关各自出蓝图+预览)")


if __name__ == "__main__":
    test_texture_palette_takeover()
    test_palette_fallback_three_levels()
    test_legacy_library_switch()
    test_build_match_config_defaults_and_passthrough()
    test_engine_config_passthrough_six_fields()
    test_dither_main_flow_2d_with_alpha()
    test_dither_off_matches_legacy_batch_path()
    test_preview_and_blueprint_share_library()
    test_engine_render_preview_uses_own_library()
    test_settings_dict_and_presets_carry_six_fields()
    test_project_roundtrip_new_fields()
    test_old_project_gets_defaults_and_keeps_unknown_fields()
    test_new_project_read_by_old_reader()
    test_custom_palette_limit_enforced()
    test_engine_end_to_end_with_preset_settings()
    test_dither_file_pipeline_both_ways()
    print("\n=== 管线 v2 测试全部通过 ===")
