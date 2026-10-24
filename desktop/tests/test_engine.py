from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from engine.contract import Blueprint, validate_blueprint, MAX_CHUNK_DIM
from engine.blocks import build_block_library
from engine.matcher import BlockMatcher, MatchConfig
from engine.input import (
    load_image, preprocess_image, PreprocessConfig, ImageInputError,
    SUPPORTED_IMAGE_EXTENSIONS, REJECTED_IMAGE_EXTENSIONS,
)
from engine.blueprint import BlueprintEngine, split_image_into_chunks
from engine.engine import BuildEngine, EngineConfig

TMP = tempfile.mkdtemp(prefix="terraria_builder_test_")
print("test dir:", TMP)


def make_solid_png(path, color, w=8, h=8, with_alpha=False):
    mode = "RGBA" if with_alpha else "RGB"
    arr = np.zeros((h, w, 4 if with_alpha else 3), dtype=np.uint8)
    arr[..., :3] = color
    if with_alpha:
        arr[..., 3] = 255
    Image.fromarray(arr, mode).save(path)


def make_alpha_png(path):
    arr = np.zeros((8, 8, 4), dtype=np.uint8)
    arr[..., :3] = (139, 111, 75)
    arr[:, :4, 3] = 0
    arr[:, 4:, 3] = 255
    Image.fromarray(arr, "RGBA").save(path)


def test_contract_roundtrip():
    from engine.contract import ChunkEntry, BlockEntry, BlueprintMetadata
    from datetime import datetime, timezone
    bp = Blueprint(width=2, height=2, chunk_size=2)
    bp.chunks = [
        ChunkEntry(
            chunk_index=0, offset_x=0, offset_y=0, width=2, height=2,
            blocks=[
                BlockEntry(0, 0, "Dirt", 0), BlockEntry(1, 0, "Stone", 0),
                BlockEntry(0, 1, "Wood", 0), BlockEntry(1, 1, "Dirt", 0),
            ],
        )
    ]
    bp.metadata = BlueprintMetadata(
        total_blocks=4, total_chunks=1, match_mode="precise",
        processing_time_ms=5.0,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_image={}, config={},
    )
    errs = validate_blueprint(bp)
    assert not errs, errs
    d = bp.to_dict()
    bp2 = Blueprint.from_dict(d)
    assert bp2.width == 2 and bp2.height == 2
    assert len(bp2.chunks) == 1
    assert bp2.chunks[0].blocks[0].block_id == "Dirt"
    print("PASS test_contract_roundtrip")


def test_supported_and_rejected_extensions():
    assert ".png" in SUPPORTED_IMAGE_EXTENSIONS
    assert ".jpg" in SUPPORTED_IMAGE_EXTENSIONS
    assert ".jpeg" in SUPPORTED_IMAGE_EXTENSIONS
    for ext in REJECTED_IMAGE_EXTENSIONS:
        assert ext not in SUPPORTED_IMAGE_EXTENSIONS
    print("PASS test_supported_and_rejected_extensions")


def test_reject_webp():
    path = os.path.join(TMP, "a.webp")
    make_solid_png(path, (100, 100, 100))
    try:
        load_image(path)
        assert False, "应当拒绝 webp 后缀"
    except ImageInputError as e:
        assert "webp" in str(e) or "不支持" in str(e)
    print("PASS test_reject_webp")


def test_transparency_skipped():
    path = os.path.join(TMP, "alpha.png")
    make_alpha_png(path)
    res = load_image(path)
    assert res.alpha_mask is not None
    assert bool(res.alpha_mask[0, 0]) is False
    assert bool(res.alpha_mask[0, 7]) is True
    print("PASS test_transparency_skipped")


def test_preprocess_crop_and_scale():
    path = os.path.join(TMP, "src.png")
    make_solid_png(path, (139, 111, 75), w=16, h=16)
    cfg = PreprocessConfig(crop=(2, 2, 10, 10), scale=0.5)
    res = preprocess_image(path, cfg)
    assert res.width == 4 and res.height == 4, (res.width, res.height)
    print("PASS test_preprocess_crop_and_scale")


def test_preprocess_rotate_flip():
    path = os.path.join(TMP, "src.png")
    make_solid_png(path, (139, 111, 75), w=8, h=4)
    cfg = PreprocessConfig(rotate90=1, flip_horizontal=True)
    res = preprocess_image(path, cfg)
    assert res.width == 4 and res.height == 8, (res.width, res.height)
    print("PASS test_preprocess_rotate_flip")


def test_full_pipeline_small():
    path = os.path.join(TMP, "small.png")
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    lib = build_block_library()
    dirt = lib["Dirt"].main_rgb
    stone = lib["Stone"].main_rgb
    wood = lib["Wood"].main_rgb
    arr[0:2, 0:2] = dirt
    arr[0:2, 2:4] = stone
    arr[2:4, 0:2] = wood
    arr[2:4, 2:4] = dirt
    Image.fromarray(arr).save(path)

    eng = BuildEngine(EngineConfig(match_mode="precise", chunk_size=4))
    result = eng.build_from_file(path)
    bp = result.blueprint
    errs = validate_blueprint(bp)
    assert not errs, errs
    assert bp.width == 4 and bp.height == 4
    assert bp.metadata.total_blocks == 16
    assert bp.metadata.total_chunks == 1
    grid = result.matched_grid
    assert grid[0, 0] == "Dirt"
    assert grid[0, 3] == "Stone"
    assert grid[3, 0] == "Wood"
    print("PASS test_full_pipeline_small", "blocks:", result.total_blocks, "time_ms:", round(result.processing_time_ms, 1))


def test_three_modes():
    lib = build_block_library()
    m_p = BlockMatcher(lib, MatchConfig(mode="precise"))
    m_n = BlockMatcher(lib, MatchConfig(mode="natural"))
    m_i = BlockMatcher(lib, MatchConfig(mode="minimal"))
    rgb = (150, 130, 90)
    rp = m_p.match_pixel(rgb)
    rn = m_n.match_pixel(rgb)
    ri = m_i.match_pixel(rgb)
    assert rp is not None and rn is not None and ri is not None
    assert ri.block_id in {"Dirt", "Stone", "Wood", "WoodPlank", "Sand", "Grass", "Brick", "Copper", "Iron"}
    print("PASS test_three_modes", rp.block_id, rn.block_id, ri.block_id)


def test_blacklist_whitelist():
    lib = build_block_library()
    m1 = BlockMatcher(lib, MatchConfig(mode="precise", blacklist={"Dirt"}))
    r1 = m1.match_pixel(lib["Dirt"].main_rgb)
    assert r1 is not None and r1.block_id != "Dirt", r1
    m2 = BlockMatcher(lib, MatchConfig(mode="precise", whitelist={"Stone"}))
    r2 = m2.match_pixel((100, 100, 100))
    assert r2.block_id == "Stone", r2
    print("PASS test_blacklist_whitelist", r1.block_id, r2.block_id)


def test_common_priority():
    lib = build_block_library()
    m_on = BlockMatcher(lib, MatchConfig(mode="precise", common_priority=True, common_bonus=5.0))
    m_off = BlockMatcher(lib, MatchConfig(mode="precise", common_priority=False))
    r_on = m_on.match_pixel(lib["Dirt"].main_rgb)
    r_off = m_off.match_pixel(lib["Dirt"].main_rgb)
    assert r_on.block_id == "Dirt" and r_off.block_id == "Dirt"
    print("PASS test_common_priority")


def test_chunking_large_image():
    w, h = 300, 200
    grid = np.full((h, w), "Dirt", dtype=object)
    mask = np.ones((h, w), dtype=bool)
    eng = BlueprintEngine(chunk_size=128)
    cfg = MatchConfig(mode="precise")
    bp = eng.grid_to_blueprint(grid, mask, cfg, processing_time_ms=1.0, source_info={}, match_mode="precise")
    errs = validate_blueprint(bp)
    assert not errs, errs
    assert len(bp.chunks) == 6, len(bp.chunks)
    assert bp.metadata.total_blocks == 300 * 200
    assert bp.metadata.total_chunks == 6
    for c in bp.chunks:
        assert c.width <= 128 and c.height <= 128
    print("PASS test_chunking_large_image", "chunks:", len(bp.chunks), "blocks:", bp.metadata.total_blocks)


def test_chunk_256_limit():
    eng = BlueprintEngine(chunk_size=256)
    h = w = 300
    grid = np.full((h, w), "Dirt", dtype=object)
    mask = np.ones((h, w), dtype=bool)
    cfg = MatchConfig(mode="precise")
    bp = eng.grid_to_blueprint(grid, mask, cfg, processing_time_ms=1.0, source_info={}, match_mode="precise")
    for c in bp.chunks:
        assert c.width <= MAX_CHUNK_DIM and c.height <= MAX_CHUNK_DIM
    print("PASS test_chunk_256_limit", "chunks:", len(bp.chunks))


def test_blueprint_save_load():
    path = os.path.join(TMP, "bp.json")
    eng = BlueprintEngine(chunk_size=16)
    grid = np.full((16, 16), "Stone", dtype=object)
    mask = np.ones((16, 16), dtype=bool)
    cfg = MatchConfig(mode="precise")
    bp = eng.grid_to_blueprint(grid, mask, cfg, processing_time_ms=2.0, source_info={}, match_mode="precise")
    BlueprintEngine.save_blueprint(bp, path)
    bp2 = BlueprintEngine.load_blueprint(path)
    assert bp2.width == 16 and bp2.height == 16
    assert bp2.metadata.total_blocks == 256
    errs = validate_blueprint(bp2)
    assert not errs, errs
    print("PASS test_blueprint_save_load")


def test_large_file_warning():
    from engine import input as input_mod
    assert input_mod.LARGE_FILE_THRESHOLD == 5 * 1024 * 1024
    print("PASS test_large_file_warning (constant check)")


if __name__ == "__main__":
    test_contract_roundtrip()
    test_supported_and_rejected_extensions()
    test_reject_webp()
    test_transparency_skipped()
    test_preprocess_crop_and_scale()
    test_preprocess_rotate_flip()
    test_full_pipeline_small()
    test_three_modes()
    test_blacklist_whitelist()
    test_common_priority()
    test_chunking_large_image()
    test_chunk_256_limit()
    test_blueprint_save_load()
    test_large_file_warning()
    print("\n=== 所有测试通过 ===")
