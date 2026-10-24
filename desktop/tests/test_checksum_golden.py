# -*- coding: utf-8 -*-
"""校验和「黄金向量」测试：把跨语言协议的字节级口径钉死。

为什么需要这个：
校验和是 Python（外端）与 C#（模组）各自实现的同一套规则：
  · 移除 Checksum 字段
  · 递归按键名 Ordinal 升序排序（数组保持元素顺序）
  · 紧凑 JSON（分隔符 , : 且非 ASCII 不转义）
  · UTF-8 → SHA-256 → 小写十六进制

任何一边被人手滑改了排序或空格，表现都是「所有指令都被拒 -> ExecutionError」，
是最难定位的一类故障。这里用一组固定输入 + 固定哈希做回归；
C# 侧在 ChecksumValidator.SelfCheck() 里用同一组向量自检（模组加载时跑一次）。
两边预期值必须一致，改规则必须同时改这两处。
"""

from __future__ import annotations

import json

from engine.contract import compute_checksum, dump_command

# 黄金向量：改动协议时同步更新这里与 C# 的 ChecksumValidator.SelfCheck
GOLDEN_COMMAND = {
    "Type": "PlaceBlocks",
    "OriginX": 4200, "OriginY": 430,
    "ChunkCount": 1, "TotalBlocks": 3,
    "Chunks": [{"Index": 0, "DelayMs": 200,
                "Blocks": [{"X": 0, "Y": 0, "TileId": 0},
                           {"X": 1, "Y": 0, "TileId": 1},
                           {"X": 2, "Y": 0, "TileId": 53}]}],
    "DelayBetweenChunksMs": 200, "DelayWithinChunkMs": 0,
    "MaxTotalBlocks": 0, "SkipExistingBlocks": False,
    "Timestamp": 1792718267209,
}
GOLDEN_CHECKSUM = "99facb66a94fdec4a304aba7023da66ff455e9607432dd8450716f5705e464ac"


def test_golden_checksum_stable():
    """固定输入必须稳定产出固定哈希。"""
    assert compute_checksum(GOLDEN_COMMAND) == GOLDEN_CHECKSUM, "校验和口径已漂移，检查排序/空格规则"


def test_checksum_ignores_field_order():
    """键顺序不同但内容相同的两个 dict 必须同哈希（递归排序的证明）。"""
    shuffled = dict(reversed(list(GOLDEN_COMMAND.items())))
    assert compute_checksum(shuffled) == GOLDEN_CHECKSUM


def test_dump_command_is_compact_and_sorted():
    """序列化后的紧凑 JSON 必须键有序、分隔符无空格，且总长度固定。"""
    text = dump_command(GOLDEN_COMMAND)
    assert " " not in text, "紧凑 JSON 不应含空格"
    keys = json.loads(text).keys()
    assert list(keys) == sorted(keys), "顶层键必须升序"
    assert len(text) == 328, f"规范长度变了：{len(text)}（改协议要同步更新此断言）"


if __name__ == "__main__":
    test_golden_checksum_stable()
    print("PASS test_golden_checksum_stable")
    test_checksum_ignores_field_order()
    print("PASS test_checksum_ignores_field_order")
    test_dump_command_is_compact_and_sorted()
    print("PASS test_dump_command_is_compact_and_sorted")
    print("\n=== 校验和黄金向量测试通过 ===")
