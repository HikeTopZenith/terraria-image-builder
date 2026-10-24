from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.contract import (
    Blueprint,
    BlueprintMetadata,
    BlockEntry,
    ChunkEntry,
    blueprint_to_place_command,
    blueprint_to_place_commands,
    PlaceCommandConfig,
    compute_checksum,
    validate_blueprint,
)
from engine.blocks import BLOCK_NAME_TO_TILE_ID, get_tile_id


def make_chunk(chunk_index=0, offset_x=0, offset_y=0, w=4, h=3):
    return ChunkEntry(
        chunk_index=chunk_index,
        offset_x=offset_x,
        offset_y=offset_y,
        width=w,
        height=h,
        blocks=[
            BlockEntry(0, 0, "Dirt", chunk_index),
            BlockEntry(1, 0, "Stone", chunk_index),
            BlockEntry(2, 0, "Wood", chunk_index),
        ],
    )


def test_tile_id_mapping_exists():
    # 期望值对齐 blocks.py 的 BLOCK_NAME_TO_TILE_ID（反射自 Terraria.ID.TileID 的实测值）：
    # Dirt=0 / Stone=1 / WoodBlock=30 —— 旧断言 3 / 176 是错的，原版 TileID.Dirt 就是 0。
    assert BLOCK_NAME_TO_TILE_ID["Dirt"] == 0
    assert BLOCK_NAME_TO_TILE_ID["Stone"] == 1
    assert BLOCK_NAME_TO_TILE_ID["Wood"] == 30
    assert get_tile_id("Dirt") == 0
    assert get_tile_id("NotARealBlock") == -1
    # 逐条核对本文件用到的映射，防止再次与 blocks.py 漂移
    assert BLOCK_NAME_TO_TILE_ID["Sand"] == 53
    assert BLOCK_NAME_TO_TILE_ID["Grass"] == 2
    print("PASS test_tile_id_mapping_exists")


def test_single_block_field_conversion():
    cmd = blueprint_to_place_command(make_chunk())
    assert cmd["Type"] == "PlaceBlocks"
    assert cmd["ChunkIndex"] == 0
    assert cmd["OriginX"] == 0
    assert cmd["OriginY"] == 0
    # 单方块字段：X, Y, TileId；无 chunk_index
    b0 = cmd["Blocks"][0]
    assert b0 == {"X": 0, "Y": 0, "TileId": 0}, b0  # Dirt = TileID.Dirt = 0
    assert "chunk_index" not in b0
    assert "block_id" not in b0
    b1 = cmd["Blocks"][1]
    assert b1["TileId"] == 1  # Stone
    b2 = cmd["Blocks"][2]
    assert b2["TileId"] == 30  # Wood = TileID.WoodBlock = 30
    print("PASS test_single_block_field_conversion")


def test_batch_config_fields():
    cfg = PlaceCommandConfig(
        delay_between_chunks_ms=1234,
        delay_within_chunk_ms=55,
        skip_existing_blocks=False,
        max_total_blocks=99999,
    )
    cmd = blueprint_to_place_command(make_chunk(), cfg)
    assert cmd["DelayBetweenChunksMs"] == 1234
    assert cmd["DelayWithinChunkMs"] == 55
    assert cmd["SkipExistingBlocks"] is False
    assert cmd["MaxTotalBlocks"] == 99999
    print("PASS test_batch_config_fields")


def test_timestamp_and_checksum():
    cmd = blueprint_to_place_command(make_chunk())
    assert isinstance(cmd["Timestamp"], int)
    assert cmd["Timestamp"] > 0
    assert isinstance(cmd["Checksum"], str)
    assert len(cmd["Checksum"]) == 64  # SHA-256 hex
    # 校验和覆盖除 Checksum 外的所有字段
    payload = {k: v for k, v in cmd.items() if k != "Checksum"}
    assert compute_checksum(cmd) == cmd["Checksum"]
    # 篡改 Blocks 后校验和应变化
    cmd2 = dict(cmd)
    cmd2["Blocks"] = cmd["Blocks"][:1]
    assert compute_checksum(cmd2) != cmd["Checksum"]
    print("PASS test_timestamp_and_checksum")


def test_dict_input_support():
    # 兼容 dict 输入
    chunk_dict = {
        "chunk_index": 2,
        "offset_x": 10,
        "offset_y": 20,
        "width": 4,
        "height": 3,
        "blocks": [
            {"x": 0, "y": 0, "block_id": "Sand", "chunk_index": 2},
            {"x": 1, "y": 0, "block_id": "Grass", "chunk_index": 2},
        ],
    }
    cmd = blueprint_to_place_command(chunk_dict)
    assert cmd["ChunkIndex"] == 2
    assert cmd["OriginX"] == 10
    assert cmd["OriginY"] == 20
    assert cmd["Blocks"][0] == {"X": 0, "Y": 0, "TileId": 53}  # Sand = TileID.Sand
    assert cmd["Blocks"][1] == {"X": 1, "Y": 0, "TileId": 2}   # Grass = TileID.Grass
    print("PASS test_dict_input_support")


def test_top_level_commands():
    bp = Blueprint(
        width=8, height=6, chunk_size=4,
        chunks=[
            make_chunk(0, 0, 0),
            make_chunk(1, 4, 0),
        ],
        metadata=BlueprintMetadata(
            total_blocks=6, total_chunks=2, match_mode="precise",
            processing_time_ms=1.0, created_at="now", source_image={}, config={},
        ),
    )
    errs = validate_blueprint(bp)
    assert not errs, errs
    commands = blueprint_to_place_commands(bp)
    assert len(commands) == 2
    assert commands[0]["ChunkIndex"] == 0
    assert commands[1]["ChunkIndex"] == 1
    assert commands[1]["OriginX"] == 4
    for c in commands:
        assert c["Type"] == "PlaceBlocks"
        assert "Checksum" in c
    # 也兼容 dict 输入
    commands2 = blueprint_to_place_commands(bp.to_dict())
    assert len(commands2) == 2
    print("PASS test_top_level_commands")


def test_unknown_block_raises():
    bad_chunk = ChunkEntry(
        chunk_index=0, offset_x=0, offset_y=0, width=2, height=2,
        blocks=[BlockEntry(0, 0, "NotARealBlock", 0)],
    )
    try:
        blueprint_to_place_command(bad_chunk)
        assert False, "应当对未映射方块抛出 ValueError"
    except ValueError as e:
        assert "NotARealBlock" in str(e)
    print("PASS test_unknown_block_raises")


def test_existing_tests_still_pass():
    # 确认内部蓝图格式未变
    bp = Blueprint(width=2, height=2, chunk_size=2)
    bp.chunks = [
        ChunkEntry(
            chunk_index=0, offset_x=0, offset_y=0, width=2, height=2,
            blocks=[
                BlockEntry(0, 0, "Dirt", 0),
                BlockEntry(1, 0, "Stone", 0),
                BlockEntry(0, 1, "Wood", 0),
                BlockEntry(1, 1, "Dirt", 0),
            ],
        )
    ]
    d = bp.to_dict()
    # 内部格式字段不变
    assert d["chunks"][0]["blocks"][0] == {
        "x": 0, "y": 0, "block_id": "Dirt", "chunk_index": 0,
    }
    assert validate_blueprint(Blueprint.from_dict(d)) == []
    print("PASS test_existing_tests_still_pass")


if __name__ == "__main__":
    test_tile_id_mapping_exists()
    test_single_block_field_conversion()
    test_batch_config_fields()
    test_timestamp_and_checksum()
    test_dict_input_support()
    test_top_level_commands()
    test_unknown_block_raises()
    test_existing_tests_still_pass()
    print("\n=== 转换层测试全部通过 ===")
