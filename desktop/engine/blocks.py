"""
泰拉瑞亚原版（Vanilla）方块 RGB 色库。

每个方块记录：
- block_id: 与模组端共用的标识符（英文）
- name: 中文显示名
- main_rgb: 主色（纯色精准模式用）
- samples: 纹理采样点列表（自然过渡模式用，每个采样点为 [r, g, b]）
- common: 是否为常用基础方块（常用优先权重用）

TileID 全部取自 tModLoader 1.4.4 的 Terraria.ID.TileID 常量（反射读取），
非猜测值；颜色为原版贴图的近似采样，仅用于近似匹配。
不新增任何自定义方块、贴图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class BlockColor:
    block_id: str
    name: str
    main_rgb: tuple  # (r, g, b)
    samples: list  # [[r,g,b], ...]
    common: bool = False
    # 半透明方块（玻璃/冰块）：背后无墙时会透出纯黑背景，
    # 照片还原时会呈现为「方块内部黑色阴影」，默认排除，除非白名单显式点名。
    transparent: bool = False
    # 重力方块（TileID.Sets.Falling）：放置后会下落，会直接砸坏建筑结构，
    # 默认排除。实测命中的只有 Sand(53)，Ebonsand/Pearlsand/Silt/Slush/Crimsand 未收录。
    falling: bool = False


# 基础方块子集（极简模式可用）：泥土、石块、木头等
BASIC_BLOCK_IDS = {"Dirt", "Stone", "Wood", "WoodPlank", "Sand", "Grass", "Brick", "Copper", "Iron"}

BLOCKS: list = [
    # ---------------- 常见基础方块 ----------------
    BlockColor("Dirt", "泥土", (139, 111, 75), [[139, 111, 75], [120, 95, 65], [155, 125, 85]], common=True),
    BlockColor("Stone", "石块", (140, 140, 140), [[140, 140, 140], [120, 120, 120], [160, 160, 160]], common=True),
    BlockColor("Wood", "木材", (160, 110, 60), [[160, 110, 60], [140, 95, 50], [180, 125, 70]], common=True),
    BlockColor("WoodPlank", "木板", (180, 130, 70), [[180, 130, 70], [160, 115, 60], [200, 145, 80]], common=True),
    BlockColor("StoneBrick", "灰砖", (150, 150, 150), [[150, 150, 150], [130, 130, 130], [170, 170, 170]], common=True),
    # 沙子是重力方块（TileID.Sets.Falling[53] = true），会掉落砸坏结构，标记后默认不参与匹配；
    # 需要沙色时由 Sandstone / HardenedSand 承接。
    BlockColor("Sand", "沙子", (230, 220, 150), [[230, 220, 150], [210, 200, 130], [245, 235, 170]], common=True, falling=True),
    BlockColor("Grass", "草方块", (80, 150, 60), [[80, 150, 60], [70, 135, 55], [95, 165, 70]], common=True),
    BlockColor("Brick", "红砖", (180, 60, 50), [[180, 60, 50], [160, 55, 45], [200, 70, 60]], common=True),
    BlockColor("Ash", "灰烬", (90, 90, 90), [[90, 90, 90], [75, 75, 75], [105, 105, 105]], common=True),

    # ---------------- 矿石方块 ----------------
    BlockColor("Copper", "铜矿", (195, 110, 70), [[195, 110, 70], [175, 95, 60], [215, 125, 80]]),
    BlockColor("Iron", "铁矿", (150, 150, 160), [[150, 150, 160], [130, 130, 140], [170, 170, 180]]),
    BlockColor("Silver", "银矿", (190, 200, 210), [[190, 200, 210], [170, 180, 190], [210, 220, 230]]),
    BlockColor("Gold", "金矿", (235, 200, 50), [[235, 200, 50], [215, 185, 45], [250, 215, 60]]),
    BlockColor("Diamond", "钻石", (150, 230, 255), [[150, 230, 255], [130, 210, 235], [170, 250, 255]]),

    # ---------------- 建筑/装饰方块 ----------------
    BlockColor("Glass", "玻璃", (200, 220, 240), [[200, 220, 240], [180, 200, 220], [220, 240, 255]], transparent=True),
    BlockColor("Clay", "黏土", (170, 150, 120), [[170, 150, 120], [150, 135, 105], [190, 165, 135]]),
    BlockColor("WoodEbon", "乌木", (40, 30, 35), [[40, 30, 35], [30, 25, 30], [55, 40, 45]]),
    BlockColor("WoodPearl", "珍珠木", (220, 235, 245), [[220, 235, 245], [200, 215, 225], [240, 255, 255]]),
    BlockColor("WoodShade", "暗影木", (50, 40, 60), [[50, 40, 60], [40, 30, 50], [65, 50, 75]]),

    # ---------------- 特殊/杂项 ----------------
    BlockColor("Snow", "雪块", (240, 245, 250), [[240, 245, 250], [225, 230, 235], [255, 255, 255]]),
    BlockColor("Ice", "冰块", (180, 220, 255), [[180, 220, 255], [160, 200, 235], [200, 240, 255]], transparent=True),
    BlockColor("Hellstone", "地狱石", (160, 40, 30), [[160, 40, 30], [140, 35, 25], [180, 55, 45]]),
    BlockColor("Meteorite", "陨石", (60, 50, 90), [[60, 50, 90], [50, 40, 80], [75, 65, 105]]),
    BlockColor("Lihzahrd", "神庙砖", (200, 130, 50), [[200, 130, 50], [180, 115, 45], [220, 145, 65]]),
    BlockColor("Mushroom", "蘑菇方块", (140, 60, 160), [[140, 60, 160], [120, 50, 140], [160, 75, 180]]),
    BlockColor("LivingWood", "活木", (150, 120, 80), [[150, 120, 80], [130, 105, 70], [170, 135, 90]]),
    BlockColor("PalmWood", "棕榈木", (186, 162, 118), [[186, 162, 118], [168, 146, 104], [204, 180, 132]]),

    # ---------------- 地形补充（提升照片匹配覆盖） ----------------
    BlockColor("Obsidian", "黑曜石", (35, 25, 50), [[35, 25, 50], [25, 18, 38], [50, 38, 68]]),
    BlockColor("Mud", "泥浆", (60, 45, 35), [[60, 45, 35], [48, 36, 28], [75, 58, 45]]),
    BlockColor("Sandstone", "沙岩", (214, 194, 142), [[214, 194, 142], [196, 176, 126], [230, 210, 158]]),
    BlockColor("HardenedSand", "硬化沙", (200, 180, 126), [[200, 180, 126], [182, 162, 112], [216, 196, 142]]),
    BlockColor("Ebonstone", "黑檀石", (60, 50, 70), [[60, 50, 70], [48, 40, 58], [75, 64, 86]]),
    BlockColor("Pearlstone", "珍珠石", (200, 200, 215), [[200, 200, 215], [182, 182, 198], [218, 218, 232]]),
    BlockColor("Crimstone", "猩红石", (120, 40, 60), [[120, 40, 60], [103, 32, 50], [140, 52, 74]]),
    BlockColor("JungleGrass", "丛林草", (60, 130, 70), [[60, 130, 70], [50, 112, 58], [74, 148, 84]]),

    # ---------------- 色域补缺（迭代2） ----------------
    # 原版色库的两个硬缺口：缺「亮中性（白）」与「高饱和红」，导致白骨骼发灰、
    # 红血肉只能落到砖红上。下列方块由游戏自报的 TileID 名称表核对，
    # 并从 763 张贴图里按实心帧固有色数据驱动筛出（n>=11 表示是规整的方块贴图）。
    # 这里的 rgb 只是占位，运行时一律由 palette_auto 的 solid_rgb 覆盖。
    BlockColor("ShimmerBrick", "微光砖", (191, 211, 213), [[191, 211, 213]]),           # L*83 C*7 亮中性
    BlockColor("FleshBlock", "血肉块", (147, 31, 43), [[147, 31, 43]]),                 # L*33 C*53 高饱和红
    BlockColor("RedStucco", "红灰泥", (222, 130, 116), [[222, 130, 116]]),              # L*64 C*41 中亮红
    BlockColor("RedDynastyShingles", "红王朝瓦", (153, 62, 55), [[153, 62, 55]]),        # L*39 C*44 中暗红
    BlockColor("HellstoneBrick", "狱石砖", (126, 50, 43), [[126, 50, 43]]),             # L*32 C*39 暗暖红
    BlockColor("LunarBlockNebula", "星云块", (128, 89, 190), [[128, 89, 190]]),          # L*46 C*60 高饱和紫
    BlockColor("AncientBlueBrick", "远古蓝砖", (77, 79, 150), [[77, 79, 150]]),          # L*37 C*44 深蓝紫
]


# ---------------- 贴图固有色接管（唯一色库入口） ----------------
# main_rgb / samples 由 palette_auto 的「实心整块帧」无光照固有色覆盖。
# 三级回退：solid_rgb -> avg_rgb -> 本表硬编码 main_rgb；
#           贴图 samples -> 本表硬编码采样点。
# palette_auto 模块缺失或某方块没有条目时静默回退到本表值，不抛异常。
# 匹配（matcher）与预览上色（preview）都必须从这里取色，否则预览与蓝图会分叉。


def _texture_entry(block_id: str):
    """取 palette_auto 里该方块的贴图条目；模块或条目缺失时返回 None。"""
    try:
        from .palette_auto import get_entry
    except Exception:
        return None
    try:
        return get_entry(block_id)
    except Exception:
        return None


def _entry_rgb(entry):
    """条目固有色：solid_rgb 优先，缺失回退 avg_rgb，两者都缺失返回 None。"""
    if not entry:
        return None
    for key in ("solid_rgb", "avg_rgb"):
        rgb = entry.get(key)
        if rgb is not None and len(rgb) == 3:
            return tuple(int(c) for c in rgb)
    return None


def _entry_samples(entry) -> list:
    """条目里的贴图采样点；缺失或格式非法时返回空列表。"""
    if not entry:
        return []
    out = []
    for s in entry.get("samples") or []:
        if s is not None and len(s) == 3:
            out.append([int(c) for c in s])
    return out


def build_block_library(use_texture_palette: bool = True) -> Dict[str, BlockColor]:
    """返回 block_id -> BlockColor 的映射（全项目唯一色库入口）。

    use_texture_palette=True（默认）：main_rgb / samples 换成 palette_auto 的
    贴图实心固有色与贴图采样点，三级回退 solid_rgb -> avg_rgb -> 本表 main_rgb；
    use_texture_palette=False：强制回旧色库（本表硬编码值），供 A/B 对比与回归测试。

    两种口径仅在 main_rgb / samples 上不同，其余字段（common / transparent /
    falling 等）与 BLOCKS 完全一致；每次调用返回新对象，调用方可安全修改。
    """
    lib: Dict[str, BlockColor] = {}
    for b in BLOCKS:
        entry = _texture_entry(b.block_id) if use_texture_palette else None
        rgb = _entry_rgb(entry) or tuple(b.main_rgb)
        samples = _entry_samples(entry) or [list(s) for s in b.samples]
        lib[b.block_id] = BlockColor(
            block_id=b.block_id,
            name=b.name,
            main_rgb=tuple(rgb),
            samples=samples,
            common=b.common,
            transparent=b.transparent,
            falling=b.falling,
        )
    return lib


def get_block_ids(common_only: bool = False) -> list:
    if common_only:
        return [b.block_id for b in BLOCKS if b.common]
    return [b.block_id for b in BLOCKS]


def get_common_block_ids() -> list:
    return [b.block_id for b in BLOCKS if b.common]


# ---------------- TileID 映射 ----------------
# 方块英文名 -> tModLoader 1.4.4 原版 TileID（反射自 Terraria.ID.TileID，均为实测值）
BLOCK_NAME_TO_TILE_ID: Dict[str, int] = {
    # 基础方块
    "Dirt": 0,              # TileID.Dirt
    "Stone": 1,             # TileID.Stone
    "Grass": 2,             # TileID.Grass
    "Wood": 30,             # TileID.WoodBlock
    "WoodPlank": 30,        # 同 WoodBlock（原版无独立木板方块）
    "StoneBrick": 38,       # TileID.GrayBrick
    "Sand": 53,             # TileID.Sand
    "Brick": 39,            # TileID.RedBrick
    "Ash": 57,              # TileID.Ash

    # 矿石方块
    "Copper": 7,            # TileID.Copper
    "Iron": 6,              # TileID.Iron
    "Silver": 9,            # TileID.Silver
    "Gold": 8,              # TileID.Gold
    "Diamond": 68,          # TileID.Diamond

    # 建筑/装饰方块
    "Glass": 54,            # TileID.Glass
    "Clay": 40,             # TileID.ClayBlock
    "WoodEbon": 157,        # TileID.Ebonwood
    "WoodPearl": 159,       # TileID.Pearlwood
    "WoodShade": 208,       # TileID.Shadewood

    # 特殊/杂项
    "Snow": 147,            # TileID.SnowBlock
    "Ice": 161,             # TileID.IceBlock
    "Hellstone": 58,        # TileID.Hellstone
    "Meteorite": 37,        # TileID.Meteorite
    "Lihzahrd": 226,        # TileID.LihzahrdBrick
    "Mushroom": 190,        # TileID.MushroomBlock
    "LivingWood": 191,      # TileID.LivingWood
    "PalmWood": 322,        # TileID.PalmWood

    # 地形补充
    "Obsidian": 56,         # TileID.Obsidian
    "Mud": 59,              # TileID.Mud
    "Sandstone": 396,       # TileID.Sandstone
    "HardenedSand": 397,    # TileID.HardenedSand
    "Ebonstone": 25,        # TileID.Ebonstone
    "Pearlstone": 117,      # TileID.Pearlstone
    "Crimstone": 203,       # TileID.Crimstone
    "JungleGrass": 60,      # TileID.JungleGrass

    # 色域补缺（TileID 名称由游戏内 TileID.Search.GetName 导出核对）
    "ShimmerBrick": 667,        # TileID.ShimmerBrick
    "FleshBlock": 195,          # TileID.FleshBlock
    "RedStucco": 153,           # TileID.RedStucco
    "RedDynastyShingles": 312,  # TileID.RedDynastyShingles
    "HellstoneBrick": 76,       # TileID.HellstoneBrick
    "LunarBlockNebula": 417,    # TileID.LunarBlockNebula
    "AncientBlueBrick": 677,    # TileID.AncientBlueBrick
}


def get_tile_id(block_id: str) -> int:
    """把方块英文名转换为原版 TileID；未映射返回 -1。"""
    return BLOCK_NAME_TO_TILE_ID.get(block_id, -1)


if __name__ == "__main__":
    lib = build_block_library()
    print(f"方块总数: {len(lib)}")
    print(f"常用方块: {get_common_block_ids()}")
    missing = [b.block_id for b in BLOCKS if b.block_id not in BLOCK_NAME_TO_TILE_ID]
    print(f"缺少 TileID 映射: {missing if missing else '无'}")
