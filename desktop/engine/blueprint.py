"""
大图分块与蓝图生成模块。

将匹配后的方块网格切分为不超过 256x256 的子块，按契约生成 JSON 蓝图，
并支持蓝图的导出保存与导入读取。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from .contract import (
    Blueprint,
    BlueprintMetadata,
    BlockEntry,
    ChunkEntry,
    MAX_CHUNK_DIM,
    validate_blueprint,
)
from .matcher import BlockMatcher, MatchConfig


class BlueprintEngine:
    """蓝图引擎：从像素网格生成分块蓝图。"""

    def __init__(self, chunk_size: int = MAX_CHUNK_DIM):
        if chunk_size <= 0 or chunk_size > MAX_CHUNK_DIM:
            raise ValueError(f"chunk_size 必须在 1..{MAX_CHUNK_DIM} 之间")
        self.chunk_size = chunk_size

    def grid_to_blueprint(
        self,
        grid: np.ndarray,           # (H, W) 字符串数组，block_id 或 ""（透明/跳过）
        alpha_mask: Optional[np.ndarray],  # (H, W) bool，True=需生成方块
        match_config: MatchConfig,
        processing_time_ms: float = 0.0,
        source_info: Optional[dict] = None,
        match_mode: str = "precise",
    ) -> Blueprint:
        """
        将匹配后的方块网格转换为带分块的蓝图。

        grid[y, x] 为 block_id；"" 表示该像素不生成方块（透明或匹配失败）。
        alpha_mask 若提供，False 的位置强制不生成方块。
        """
        h, w = grid.shape
        if alpha_mask is None:
            alpha_mask = np.ones((h, w), dtype=bool)

        # 计算分块网格
        chunks_x = (w + self.chunk_size - 1) // self.chunk_size
        chunks_y = (h + self.chunk_size - 1) // self.chunk_size
        total_chunks = chunks_x * chunks_y

        chunks: List[ChunkEntry] = []
        chunk_index = 0
        total_blocks = 0

        for cy in range(chunks_y):
            for cx in range(chunks_x):
                ox = cx * self.chunk_size
                oy = cy * self.chunk_size
                cw = min(self.chunk_size, w - ox)
                ch = min(self.chunk_size, h - oy)

                blocks: List[BlockEntry] = []
                for y in range(oy, oy + ch):
                    for x in range(ox, ox + cw):
                        if not alpha_mask[y, x]:
                            continue
                        bid = grid[y, x]
                        if not bid:
                            continue
                        blocks.append(
                            BlockEntry(
                                x=x - ox,
                                y=y - oy,
                                block_id=bid,
                                chunk_index=chunk_index,
                            )
                        )
                        total_blocks += 1

                chunks.append(
                    ChunkEntry(
                        chunk_index=chunk_index,
                        offset_x=ox,
                        offset_y=oy,
                        width=cw,
                        height=ch,
                        blocks=blocks,
                    )
                )
                chunk_index += 1

        bp = Blueprint(
            width=w,
            height=h,
            chunk_size=self.chunk_size,
            chunks=chunks,
            metadata=BlueprintMetadata(
                total_blocks=total_blocks,
                total_chunks=total_chunks,
                match_mode=match_mode,
                processing_time_ms=processing_time_ms,
                created_at=self._now_iso(),
                source_image=source_info or {},
                config=asdict(match_config),
            ),
        )

        errors = validate_blueprint(bp)
        if errors:
            raise ValueError(f"生成的蓝图不符合契约：{'; '.join(errors)}")
        return bp

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    # ---------------- 导出 / 导入 ----------------

    @staticmethod
    def save_blueprint(bp: Blueprint, path: str) -> None:
        """将蓝图保存为 JSON 文件。"""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bp.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_blueprint(path: str) -> Blueprint:
        """从 JSON 文件读取蓝图。"""
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return Blueprint.from_dict(d)


def split_image_into_chunks(
    arr: np.ndarray, chunk_size: int = MAX_CHUNK_DIM
) -> List[Tuple[int, int, int, int, np.ndarray]]:
    """
    将图像数组切分为子块，返回列表：
    (chunk_index, offset_x, offset_y, width, height, sub_array)
    供逐块匹配使用（可降低单次匹配内存峰值）。
    """
    h, w = arr.shape[:2]
    chunks = []
    idx = 0
    for oy in range(0, h, chunk_size):
        for ox in range(0, w, chunk_size):
            cw = min(chunk_size, w - ox)
            ch = min(chunk_size, h - oy)
            sub = arr[oy : oy + ch, ox : ox + cw]
            chunks.append((idx, ox, oy, cw, ch, sub))
            idx += 1
    return chunks
