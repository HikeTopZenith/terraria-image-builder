"""泰拉瑞亚自动建筑系统 - 核心计算引擎（外部桌面端）。

包含：
- contract.py：与模组端共享的 JSON 蓝图通信契约
- blocks.py：泰拉瑞亚原版方块 RGB 色库（TileID 取自游戏程序集）
- matcher.py：CIELAB 方块匹配引擎（三模式/黑白名单/常用优先）
- input.py：图片输入与预处理
- blueprint.py：大图分块与蓝图导入导出
- engine.py：顶层 facade，串联以上模块并输出日志
- preview.py：离线方块预览渲染（不连游戏即可出图）
- client.py：模组端 WebSocket 连接层（后台线程）
"""


__all__ = [
    "BuildEngine",
    "EngineConfig",
    "BuildResult",
    "build_match_config",
    "build_block_library",
    "Blueprint",
    "MatchConfig",
    "PreprocessConfig",
    "BuildClient",
    "render_grid",
    "render_blueprint",
    "blueprint_statistics",
    "blueprint_to_chunked_command",
    "dump_command",
    "save_project",
    "load_project",
    "build_settings_dict",
    "normalize_settings",
    "color_map",
    "PRESETS",
    "get_preset",
    "preset_labels",
]

from .contract import Blueprint, validate_blueprint, example_blueprint
from .blocks import build_block_library, BlockColor
from .matcher import BlockMatcher, MatchConfig
from .input import (
    PreprocessConfig,
    LoadResult,
    ImageInputError,
    load_image,
    preprocess_image,
    apply_preprocess,
    SUPPORTED_IMAGE_EXTENSIONS,
    REJECTED_IMAGE_EXTENSIONS,
)
from .blueprint import BlueprintEngine, split_image_into_chunks
from .engine import build_match_config
from .contract import (
    blueprint_to_place_command,
    blueprint_to_place_commands,
    blueprint_to_chunked_command,
    dump_command,
    save_project,
    load_project,
    build_settings_dict,
    normalize_settings,
    PlaceCommandConfig,
    compute_checksum,
)
from .blocks import BLOCK_NAME_TO_TILE_ID, get_tile_id
from .preview import render_grid, render_blueprint, blueprint_statistics, color_map
from .presets import PRESETS, DEFAULT_PRESET, get_preset, preset_labels
from .client import BuildClient
