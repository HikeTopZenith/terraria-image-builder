
"""
统一通信契约 - 泰拉瑞亚自动建筑系统 (Terraria Auto Builder)

外部桌面端（图像处理与方块匹配引擎）与 tModLoader 1.4.4 模组端
通过本地 WebSocket 或 JSON 文件通信，共享本契约定义的蓝图格式。

本阶段（阶段3）只负责生成与解析该 JSON 蓝图，不负责传输与执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import os
import time
import hashlib
import json

from .blocks import BLOCK_NAME_TO_TILE_ID
from .presets import NEW_MATCH_DEFAULTS

PROTOCOL_NAME = "terraria_builder"
PROTOCOL_VERSION = "1.0"

MAX_CHUNK_DIM = 256

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
REJECTED_IMAGE_EXTENSIONS = {".webp", ".heic", ".gif", ".psd"}


@dataclass
class BlockEntry:
    """单个方块条目：相对坐标 + 方块 ID + 所属分块索引。"""

    x: int
    y: int
    block_id: str
    chunk_index: int


@dataclass
class ChunkEntry:
    """单个分块。"""

    chunk_index: int
    offset_x: int
    offset_y: int
    width: int
    height: int
    blocks: list = field(default_factory=list)


@dataclass
class BlueprintMetadata:
    total_blocks: int
    total_chunks: int
    match_mode: str
    processing_time_ms: float
    created_at: str
    source_image: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)




def _jsonable(obj):
    """递归将 set 转为 sorted list，使配置可 JSON 序列化。"""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


@dataclass
class Blueprint:
    """完整蓝图。可直接 JSON 序列化发送给模组端，或从模组端 JSON 反序列化。"""

    protocol: str = PROTOCOL_NAME
    version: str = PROTOCOL_VERSION
    width: int = 0
    height: int = 0
    chunk_size: int = MAX_CHUNK_DIM
    chunks: list = field(default_factory=list)
    metadata: Optional[BlueprintMetadata] = None

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "chunk_size": self.chunk_size,
            "chunks": [
                {
                    "chunk_index": c.chunk_index,
                    "offset_x": c.offset_x,
                    "offset_y": c.offset_y,
                    "width": c.width,
                    "height": c.height,
                    "blocks": [
                        {
                            "x": b.x,
                            "y": b.y,
                            "block_id": b.block_id,
                            "chunk_index": b.chunk_index,
                        }
                        for b in c.blocks
                    ],
                }
                for c in self.chunks
            ],
            "metadata": _jsonable(asdict(self.metadata)) if self.metadata else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "Blueprint":
        chunks = []
        for c in d.get("chunks", []):
            chunks.append(
                ChunkEntry(
                    chunk_index=c["chunk_index"],
                    offset_x=c["offset_x"],
                    offset_y=c["offset_y"],
                    width=c["width"],
                    height=c["height"],
                    blocks=[
                        BlockEntry(
                            x=b["x"],
                            y=b["y"],
                            block_id=b["block_id"],
                            chunk_index=b["chunk_index"],
                        )
                        for b in c.get("blocks", [])
                    ],
                )
            )
        meta = None
        if d.get("metadata"):
            # 向前兼容：新版本写入的未知 metadata 字段一律忽略，不让旧代码崩溃
            known = {f.name for f in fields(BlueprintMetadata)}
            meta = BlueprintMetadata(
                **{k: v for k, v in d["metadata"].items() if k in known}
            )
        return Blueprint(
            protocol=d.get("protocol", PROTOCOL_NAME),
            version=d.get("version", PROTOCOL_VERSION),
            width=d["width"],
            height=d["height"],
            chunk_size=d.get("chunk_size", MAX_CHUNK_DIM),
            chunks=chunks,
            metadata=meta,
        )


def validate_blueprint(bp: Blueprint) -> list:
    """校验蓝图是否符合契约，返回错误信息列表（空表示通过）。"""
    errors: list = []
    if bp.protocol != PROTOCOL_NAME:
        errors.append(f"protocol 必须为 {PROTOCOL_NAME}")
    if bp.version != PROTOCOL_VERSION:
        errors.append(f"version 必须为 {PROTOCOL_VERSION}")
    if bp.width <= 0 or bp.height <= 0:
        errors.append("width/height 必须大于 0")
    if bp.chunk_size <= 0 or bp.chunk_size > MAX_CHUNK_DIM:
        errors.append(f"chunk_size 必须在 1..{MAX_CHUNK_DIM} 之间")
    seen_indices = set()
    for c in bp.chunks:
        if c.chunk_index in seen_indices:
            errors.append(f"分块索引重复: {c.chunk_index}")
        seen_indices.add(c.chunk_index)
        if c.width <= 0 or c.height <= 0:
            errors.append(f"分块 {c.chunk_index} 尺寸非法")
        if c.width > MAX_CHUNK_DIM or c.height > MAX_CHUNK_DIM:
            errors.append(f"分块 {c.chunk_index} 超过单批次上限 {MAX_CHUNK_DIM}")
        for b in c.blocks:
            if b.chunk_index != c.chunk_index:
                errors.append(f"方块 chunk_index 与所属分块不一致: {b.chunk_index} vs {c.chunk_index}")
            if not (0 <= b.x < c.width and 0 <= b.y < c.height):
                errors.append(f"方块坐标越界: ({b.x},{b.y}) 在分块 {c.chunk_index}")
            if not b.block_id:
                errors.append(f"方块 ({b.x},{b.y}) block_id 为空")
    if bp.metadata and bp.metadata.total_blocks != sum(len(c.blocks) for c in bp.chunks):
        errors.append("metadata.total_blocks 与实际方块数不一致")
    if bp.metadata and bp.metadata.total_chunks != len(bp.chunks):
        errors.append("metadata.total_chunks 与实际分块数不一致")
    return errors


def example_blueprint() -> str:
    """返回一个最小示例蓝图的 JSON 字符串，供双端对照。"""
    import json

    bp = Blueprint(
        width=4,
        height=3,
        chunk_size=4,
        chunks=[
            ChunkEntry(
                chunk_index=0,
                offset_x=0,
                offset_y=0,
                width=4,
                height=3,
                blocks=[
                    BlockEntry(0, 0, "Dirt", 0),
                    BlockEntry(1, 0, "Dirt", 0),
                    BlockEntry(2, 0, "Stone", 0),
                    BlockEntry(3, 0, "Stone", 0),
                    BlockEntry(0, 1, "Wood", 0),
                    BlockEntry(1, 1, "Wood", 0),
                    BlockEntry(2, 1, "Dirt", 0),
                    BlockEntry(3, 1, "Dirt", 0),
                    BlockEntry(0, 2, "Stone", 0),
                    BlockEntry(1, 2, "Stone", 0),
                    BlockEntry(2, 2, "Wood", 0),
                    BlockEntry(3, 2, "Wood", 0),
                ],
            )
        ],
        metadata=BlueprintMetadata(
            total_blocks=12,
            total_chunks=1,
            match_mode="precise",
            processing_time_ms=12.5,
            created_at=datetime.now(timezone.utc).isoformat(),
            source_image={"path": "example.png", "width": 4, "height": 3, "size_bytes": 1024},
            config={},
        ),
    )
    return json.dumps(bp.to_dict(), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(example_blueprint())
    print()
    import json as _j

    print(
        "contract self-check passed:",
        not validate_blueprint(Blueprint.from_dict(_j.loads(example_blueprint()))),
    )


# ---------------- 模组端 PlaceBlocks 指令适配 ----------------

@dataclass
class PlaceCommandConfig:
    """生成 PlaceBlocks 指令的全局配置参数。"""

    delay_between_chunks_ms: int = 500   # 分块间延时（毫秒）
    delay_within_chunk_ms: int = 10      # 分块内单个方块放置延时（毫秒）
    skip_existing_blocks: bool = True    # 是否跳过已存在的方块
    max_total_blocks: int = 0            # 总方块上限（0 = 不限制）


def _canonical_json(obj) -> str:
    """生成稳定规范化的 JSON 字符串（键排序、紧凑分隔符），用于校验和。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_checksum(command: dict) -> str:
    """计算 PlaceBlocks 指令的校验和。

    规则：对指令中除 Checksum 外的字段（含 Timestamp）做规范化 JSON，
    取 SHA-256 十六进制小写。模组端按同一规则校验，用于 JSON 轮询
    防半写、防重放、防篡改。
    """
    payload = {k: v for k, v in command.items() if k != "Checksum"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _tile_id_for(block_id: str) -> int:
    """把方块英文名转换为原版 TileID；未映射时抛出 ValueError。"""
    if block_id not in BLOCK_NAME_TO_TILE_ID:
        raise ValueError(f"方块 {block_id!r} 不在 TileID 映射表中")
    return BLOCK_NAME_TO_TILE_ID[block_id]


def blueprint_to_place_command(chunk, config=None) -> dict:
    """将单个分块的内部蓝图数据转换为模组端 PlaceBlocks 指令 JSON。

    参数:
        chunk: 单个分块数据，可以是 ChunkEntry 对象或 dict
               （含 chunk_index / offset_x / offset_y / blocks）。
        config: PlaceCommandConfig 或 dict，提供分块间延时、分块内延时、
                是否跳过已有方块、总方块上限。

    返回:
        符合模组端契约的 PlaceBlocks 指令 dict。

    字段转换规则:
        - 单方块: x -> X, y -> Y, block_id -> TileId(int), 移除 chunk_index
        - 批量: Type="PlaceBlocks", chunk_index -> ChunkIndex,
                offset_x -> OriginX, offset_y -> OriginY, blocks -> Blocks,
                新增 DelayBetweenChunksMs / DelayWithinChunkMs /
                MaxTotalBlocks / SkipExistingBlocks / Timestamp / Checksum
    """
    if config is None:
        config = PlaceCommandConfig()
    if isinstance(config, dict):
        config = PlaceCommandConfig(**config)

    # 兼容 ChunkEntry 对象与 dict 输入
    if hasattr(chunk, "chunk_index"):
        chunk_index = chunk.chunk_index
        offset_x = chunk.offset_x
        offset_y = chunk.offset_y
        blocks = list(chunk.blocks)
    else:
        chunk_index = chunk["chunk_index"]
        offset_x = chunk["offset_x"]
        offset_y = chunk["offset_y"]
        blocks = list(chunk.get("blocks", []))

    # 单方块字段转换：x -> X, y -> Y, block_id -> TileId, 移除 chunk_index
    block_entries = []
    for b in blocks:
        if hasattr(b, "x"):
            x, y, block_id = b.x, b.y, b.block_id
        else:
            x, y, block_id = b["x"], b["y"], b["block_id"]
        block_entries.append(
            {"X": int(x), "Y": int(y), "TileId": _tile_id_for(block_id)}
        )

    command = {
        "Type": "PlaceBlocks",
        "ChunkIndex": int(chunk_index),
        "OriginX": int(offset_x),
        "OriginY": int(offset_y),
        "Blocks": block_entries,
        "DelayBetweenChunksMs": int(config.delay_between_chunks_ms),
        "DelayWithinChunkMs": int(config.delay_within_chunk_ms),
        "MaxTotalBlocks": int(config.max_total_blocks),
        "SkipExistingBlocks": bool(config.skip_existing_blocks),
        "Timestamp": int(time.time() * 1000),  # Unix 毫秒时间戳
    }
    command["Checksum"] = compute_checksum(command)
    return command


def blueprint_to_place_commands(blueprint, config=None) -> list:
    """顶层便捷方法：输入完整蓝图与配置，按分块依次生成 PlaceBlocks 指令列表。

    返回可直接发送给模组端的指令列表（按 chunk_index 升序）。
    """
    if config is None:
        config = PlaceCommandConfig()
    if isinstance(config, dict):
        config = PlaceCommandConfig(**config)

    if hasattr(blueprint, "chunks"):
        chunks = list(blueprint.chunks)
    else:
        chunks = list(blueprint.get("chunks", []))

    commands = []
    for chunk in chunks:
        commands.append(blueprint_to_place_command(chunk, config))
    return commands

# ---------------------------------------------------------------------------
# 新版分块契约：一条 PlaceBlocks 携带全部 Chunks（与模组端 v1.1 完全对齐）
# ---------------------------------------------------------------------------

# 发送时统一使用的序列化参数：与 compute_checksum 的规范化口径一致，
# 保证「算校验和的那份 JSON」和「发出去的那份 JSON」字节相同。
SEND_JSON_KWARGS = dict(sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dump_command(command: dict) -> str:
    """把指令序列化为可直接发送的紧凑 JSON（键排序，与校验和口径一致）。"""
    return json.dumps(command, **SEND_JSON_KWARGS)


def blueprint_to_chunked_command(
    blueprint,
    config=None,
    origin_x: int = 0,
    origin_y: int = 0,
) -> dict:
    """把整份蓝图转换为一条新版 PlaceBlocks 指令（Chunks 数组）。

    坐标规则：
      - 蓝图内 block.x / block.y 是「分块内相对坐标」；
      - 这里统一换算为「相对蓝图原点的全局坐标」（offset + local），
        模组端再加 origin 偏移即可一次对齐所有分块；
      - 空分块（没有方块）会被跳过，避免无意义调度。

    参数:
        blueprint: Blueprint 对象或等价 dict。
        config:    PlaceCommandConfig 或 dict。
        origin_x / origin_y: 蓝图左上角在世界中的方块坐标（模组端放置原点）。

    返回:
        dict，含 Type / OriginX / OriginY / ChunkCount / TotalBlocks / Chunks /
        Timestamp / Checksum。
    """
    if config is None:
        config = PlaceCommandConfig()
    if isinstance(config, dict):
        config = PlaceCommandConfig(**config)

    if hasattr(blueprint, "chunks"):
        chunks = list(blueprint.chunks)
    else:
        chunks = list(blueprint.get("chunks", []))

    def _chunk_index(ch):
        return ch.chunk_index if hasattr(ch, "chunk_index") else ch["chunk_index"]

    chunk_payloads = []
    total_blocks = 0

    for chunk in sorted(chunks, key=_chunk_index):
        if hasattr(chunk, "chunk_index"):
            c_index = chunk.chunk_index
            ox, oy = chunk.offset_x, chunk.offset_y
            blocks = list(chunk.blocks)
        else:
            c_index = chunk["chunk_index"]
            ox, oy = chunk["offset_x"], chunk["offset_y"]
            blocks = list(chunk.get("blocks", []))

        entries = []
        for b in blocks:
            if hasattr(b, "x"):
                bx, by, bid = b.x, b.y, b.block_id
            else:
                bx, by, bid = b["x"], b["y"], b["block_id"]
            entries.append({
                "X": int(ox + bx),
                "Y": int(oy + by),
                "TileId": _tile_id_for(bid),
            })

        if not entries:
            continue

        total_blocks += len(entries)
        chunk_payloads.append({
            "Index": int(c_index),
            "DelayMs": int(config.delay_between_chunks_ms),
            "Blocks": entries,
        })

    command = {
        "Type": "PlaceBlocks",
        "OriginX": int(origin_x),
        "OriginY": int(origin_y),
        "ChunkCount": len(chunk_payloads),
        "TotalBlocks": int(total_blocks),
        "Chunks": chunk_payloads,
        "DelayBetweenChunksMs": int(config.delay_between_chunks_ms),
        "DelayWithinChunkMs": int(config.delay_within_chunk_ms),
        "MaxTotalBlocks": int(config.max_total_blocks),
        "SkipExistingBlocks": bool(config.skip_existing_blocks),
        "Timestamp": int(time.time() * 1000),
    }
    command["Checksum"] = compute_checksum(command)
    return command

# ---------------------------------------------------------------------------
# 工程包：蓝图 + 全部可复现参数（预处理 / 匹配 / 黑白名单 / 原点）
# 目的：换机器、隔天再开、分享给朋友时，一键还原当时的全部设置
# ---------------------------------------------------------------------------

PROJECT_FORMAT = "terraria_builder_project"
PROJECT_VERSION = "1.0"

# 新版匹配开关（.tibp / settings 向前兼容）。
# 默认值集中在 presets.NEW_MATCH_DEFAULTS，旧工程文件缺这些键时全部走默认值。
DELTA_ALGORITHM_CHOICES = ("ciede2000", "lab")
# 自定义色卡条目上限，与 matcher.MAX_CUSTOM_PALETTE 保持一致
MAX_CUSTOM_PALETTE = 50


def normalize_custom_palette(entries) -> list:
    """校验并归一化自定义色卡：[{"rgb": [r, g, b], "block_id": "Stone"}, ...]。

    超过 MAX_CUSTOM_PALETTE（50）条时**直接抛 ValueError**，不做静默截断，
    以免用户在不知情的情况下丢掉色卡尾部条目；条目格式非法同样抛 ValueError。
    """
    items = list(entries or [])
    if len(items) > MAX_CUSTOM_PALETTE:
        raise ValueError(
            f"自定义色卡最多 {MAX_CUSTOM_PALETTE} 条，当前 {len(items)} 条；请先精简色卡"
        )
    out = []
    for i, entry in enumerate(items):
        try:
            rgb = [int(c) for c in entry["rgb"]]
            block_id = str(entry["block_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f'自定义色卡第 {i + 1} 条格式错误，需要 {{"rgb": [r, g, b], "block_id": "Stone"}}'
            )
        if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
            raise ValueError(f"自定义色卡第 {i + 1} 条 rgb 非法：{rgb}")
        if not block_id:
            raise ValueError(f"自定义色卡第 {i + 1} 条 block_id 为空")
        out.append({"rgb": rgb, "block_id": block_id})
    return out


def normalize_settings(settings: Optional[dict]) -> dict:
    """补齐 6 个新字段的默认值，其余字段（含未知字段）原样保留。

    旧 .tibp / 旧 settings dict 缺字段时走默认值、不报错、不丢其它字段；
    新版本写入的额外字段也原样带过去（旧版本读到未知字段不崩，本函数同样不崩）。
    custom_palette 超限或格式非法时抛 ValueError（与 matcher 口径一致）。
    """
    out = dict(settings or {})
    for key, value in NEW_MATCH_DEFAULTS.items():
        if out.get(key) is None:
            out[key] = list(value) if isinstance(value, list) else value
    out["custom_palette"] = normalize_custom_palette(out.get("custom_palette"))
    return out


def build_settings_dict(
    match_mode: str = "precise",
    tolerance: float = 40.0,
    texture_weight: float = 0.4,
    common_priority: bool = True,
    blacklist=None,
    whitelist=None,
    allow_falling: bool = False,
    allow_transparent: bool = False,
    delta_algorithm: str = "ciede2000",
    hue_priority: bool = False,
    prefer_pure: bool = False,
    dither: bool = False,
    use_custom_palette: bool = False,
    custom_palette=None,
    preset: str = "",
    preprocess=None,
    origin=None,
) -> dict:
    """把 UI 上的全部参数收拢成一个可序列化的 settings 字典。

    除原有字段外，固定带上 6 个新字段（键名不可改）：
    delta_algorithm（"ciede2000" | "lab"，默认 ciede2000）、hue_priority、
    prefer_pure、dither、use_custom_palette、custom_palette
    （[{"rgb": [r, g, b], "block_id": "Stone"}]，默认 []，上限 50 条）。

    custom_palette 超过 50 条或格式非法时抛 ValueError，不做静默截断；
    delta_algorithm 不在可选值内同样抛 ValueError。
    """
    if delta_algorithm not in DELTA_ALGORITHM_CHOICES:
        raise ValueError(
            f"delta_algorithm 只能是 {DELTA_ALGORITHM_CHOICES}，收到 {delta_algorithm!r}"
        )
    return {
        "match_mode": match_mode,
        "tolerance": float(tolerance),
        "texture_weight": float(texture_weight),
        "common_priority": bool(common_priority),
        "blacklist": sorted(blacklist) if blacklist else [],
        "whitelist": sorted(whitelist) if whitelist else None,
        "allow_falling": bool(allow_falling),
        "allow_transparent": bool(allow_transparent),
        "delta_algorithm": str(delta_algorithm),
        "hue_priority": bool(hue_priority),
        "prefer_pure": bool(prefer_pure),
        "dither": bool(dither),
        "use_custom_palette": bool(use_custom_palette),
        "custom_palette": normalize_custom_palette(custom_palette),
        "preset": preset or "",
        "preprocess": dict(preprocess or {
            "rotate_quarters": 0,
            "flip_h": False,
            "flip_v": False,
            "brightness": 1.0,
            "contrast": 1.0,
        }),
        "origin": dict(origin or {"x": 0, "y": 0}),
    }


def export_project(blueprint, settings: dict, source_info=None) -> dict:
    """组装工程包字典（蓝图 + 参数 + 源图信息）。"""
    bp_dict = blueprint.to_dict() if hasattr(blueprint, "to_dict") else dict(blueprint)
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings or {},
        "source": source_info or {},
        "blueprint": bp_dict,
    }


def save_project(blueprint, settings: dict, path: str, source_info=None) -> None:
    """写入工程文件（UTF-8，缩进 2，便于人工查看与 diff）。

    settings 会先经过 normalize_settings：6 个新字段缺省时补默认值，
    未知字段原样保留，保证旧 settings dict 写出的 .tibp 也带全新字段。
    """
    data = export_project(blueprint, normalize_settings(settings), source_info)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_project(path: str) -> dict:
    """读取工程文件。

    返回 {"blueprint": Blueprint, "settings": dict, "source": dict}。
    兼容两种输入：完整工程包，或裸蓝图 JSON（只有蓝图字段，此时 settings 为空）。

    向前兼容：旧 .tibp 缺 6 个新字段时经 normalize_settings 补默认值，不报错；
    文件里多出的未知字段原样保留，不丢失其它字段。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("format") == PROJECT_FORMAT:
        bp_dict = data.get("blueprint", {})
        settings = normalize_settings(data.get("settings") or {})
        source = data.get("source", {})
    elif "chunks" in data:
        # 裸蓝图：当作「只有蓝图、没有参数」处理
        bp_dict, settings, source = data, {}, {}
    else:
        raise ValueError("不是有效的工程文件或蓝图 JSON")

    return {
        "blueprint": Blueprint.from_dict(bp_dict),
        "settings": settings,
        "source": source,
    }
