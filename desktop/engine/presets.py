"""
四套参数字预设。

每套预设打包了：匹配模式、容差、纹理权重、常用优先、黑白名单、
透明/重力方块开关，以及一组推荐预处理参数。

UI 只负责把预设字段写到对应控件上，不在这里做任何渲染逻辑。
"""

from __future__ import annotations

from typing import Dict, List, Optional

# 常用且易获取的基础方块（生存向预设用）
SURVIVAL_BLOCKS: List[str] = [
    "Dirt", "Stone", "Wood", "WoodPlank", "Grass", "Sand", "Brick",
    "Clay", "Glass", "Copper", "Iron", "Silver", "Gold", "Ash",
]

# 透明优先预设：把玻璃/冰块放进来，用于做窗户、水族效果
TRANSPARENT_BLOCKS: List[str] = ["Glass", "Ice"]

# 新增匹配开关的默认值（4 套预设都显式写上这 6 个键，见各预设内的同名字段）。
# contract.build_settings_dict 的默认值与此保持一致，预设 <-> settings 往返时
# 这几个字段总是显式出现（false / "ciede2000" / []），不会因缺字段而变语义。
NEW_MATCH_DEFAULTS: Dict[str, object] = {
    "delta_algorithm": "ciede2000",
    "hue_priority": False,
    "prefer_pure": False,
    "dither": False,
    "use_custom_palette": False,
    "custom_palette": [],
}

PRESETS: Dict[str, dict] = {
    "pixel_precise": {
        "label": "像素画精准",
        "desc": "严格比对主色，忠于原图色彩；适合像素画、图标、线稿",
        "match_mode": "precise",
        "tolerance": 20.0,
        "texture_weight": 0.2,
        "common_priority": False,
        "blacklist": [],
        "whitelist": None,
        "allow_falling": False,
        "allow_transparent": False,
        "delta_algorithm": "ciede2000",
        "hue_priority": False,
        "prefer_pure": False,
        "dither": False,
        "use_custom_palette": False,
        "custom_palette": [],
        "preprocess": {
            "rotate_quarters": 0,
            "flip_h": False,
            "flip_v": False,
            "brightness": 1.0,
            "contrast": 1.0,
        },
    },
    "building_simple": {
        "label": "建筑简化",
        "desc": "仅用基础方块，块数少、建造快；适合快速搭大体轮廓",
        "match_mode": "minimal",
        "tolerance": 45.0,
        "texture_weight": 0.3,
        "common_priority": True,
        "blacklist": [],
        "whitelist": None,
        "allow_falling": False,
        "allow_transparent": False,
        "delta_algorithm": "ciede2000",
        "hue_priority": False,
        "prefer_pure": False,
        "dither": False,
        "use_custom_palette": False,
        "custom_palette": [],
        "preprocess": {
            "rotate_quarters": 0,
            "flip_h": False,
            "flip_v": False,
            "brightness": 1.0,
            "contrast": 1.05,
        },
    },
    "survival_usable": {
        "label": "生存可用",
        "desc": "只用易获取的方块，避免稀有材料；适合生存档实际施工",
        "match_mode": "natural",
        "tolerance": 40.0,
        "texture_weight": 0.4,
        "common_priority": True,
        "blacklist": [],
        "whitelist": list(SURVIVAL_BLOCKS),
        "allow_falling": False,
        "allow_transparent": False,
        "delta_algorithm": "ciede2000",
        "hue_priority": False,
        "prefer_pure": False,
        "dither": False,
        "use_custom_palette": False,
        "custom_palette": [],
        "preprocess": {
            "rotate_quarters": 0,
            "flip_h": False,
            "flip_v": False,
            "brightness": 1.0,
            "contrast": 1.0,
        },
    },
    "transparent_first": {
        "label": "透明优先",
        "desc": "允许玻璃/冰块参与匹配，适合窗户、水族箱、灯光效果",
        "match_mode": "natural",
        "tolerance": 35.0,
        "texture_weight": 0.45,
        "common_priority": False,
        "blacklist": [],
        "whitelist": None,
        "allow_falling": False,
        "allow_transparent": True,
        "delta_algorithm": "ciede2000",
        "hue_priority": False,
        "prefer_pure": False,
        "dither": False,
        "use_custom_palette": False,
        "custom_palette": [],
        "preprocess": {
            "rotate_quarters": 0,
            "flip_h": False,
            "flip_v": False,
            "brightness": 1.05,
            "contrast": 1.0,
        },
    },
}

DEFAULT_PRESET = "pixel_precise"


def get_preset(key: str) -> dict:
    """取一套预设；未知 key 回退到默认预设。

    返回深一层拷贝：custom_palette / preprocess 是独立对象，UI 修改预设内容
    不会污染 PRESETS 本体（4 套预设的参数与行为保持不变）。
    """
    preset = dict(PRESETS.get(key, PRESETS[DEFAULT_PRESET]))
    preset["custom_palette"] = [dict(e) for e in (preset.get("custom_palette") or [])]
    preset["preprocess"] = dict(preset.get("preprocess") or {})
    return preset


def preset_labels() -> Dict[str, str]:
    """key -> 中文标签，供 UI 下拉框使用。"""
    return {k: v["label"] for k, v in PRESETS.items()}
